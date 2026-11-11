#!/usr/bin/env python3
"""ETL: raw_offers -> products / offers / authors / book_authors / attributes.

Дедупликация:
1. Точное совпадение по ISBN-13 (основной критерий для книг).
2. Для записей без ISBN — fuzzy match по (title, authors) через rapidfuzz.
"""
import re
import sys
import time
from collections import Counter, defaultdict

import psycopg
from rapidfuzz import fuzz

DB_URL = "postgresql://book:book_pass@localhost:5432/book_aggregator"
FUZZY_THRESHOLD = 90
SOURCE_PRIORITY = {"chitai_gorod": 0, "book24": 1, "labirint": 2}

# атрибуты, дублирующие поля products — не сохраняем в attributes
REDUNDANT_ATTRS = {
    "isbn", "id товара", "автор", "издательство", "жанр", "раздел",
    "возрастное ограничение", "переплёт", "переплет",
    "кол-во стр.", "количество страниц", "страниц", "страницы", "язык",
}


# ---------- нормализация ----------

def norm_title(s):
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[\(\[].*?[\)\]]", " ", s)  # убираем (с мерчем), [подарочное]
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_author(s):
    if not s:
        return ""
    s = s.lower().replace(".", " ")
    tokens = sorted(t for t in re.split(r"\s+", s) if t)
    return " ".join(tokens)


def split_authors(raw):
    """Разбивает склеенные имена авторов (Book24 склеивает антологии через запятую)."""
    if not raw:
        return []
    s = raw.strip()
    # склейка: >1 запятая ИЛИ строка очень длинная
    if s.count(",") >= 2 or len(s) > 80:
        parts = [p.strip() for p in s.split(",")]
        return [p for p in parts if p]
    return [s]


def is_valid_author(name):
    """Отсеиваем только явный мусор. Длинные имена — валидны."""
    if not name:
        return False
    s = name.strip()
    if not s or len(s) < 2 or len(s) > 250:
        return False
    # `;` и `&` — точно склейка, а не одиночный автор
    if re.search(r"[;&]", s):
        return False
    # слишком много слов — вряд ли это одно имя
    if len(re.findall(r"[A-Za-zА-Яа-яЁё]{2,}", s)) > 10:
        return False
    return True


# ---------- выбор канонических полей ----------

def pick_title(records):
    sorted_recs = sorted(
        records,
        key=lambda r: (SOURCE_PRIORITY.get(r.get("source"), 9), len(r.get("title") or "")),
    )
    for r in sorted_recs:
        if r.get("title"):
            return r["title"]
    return None


def pick_longest(records, field):
    vals = [r.get(field) for r in records if r.get(field)]
    return max(vals, key=len) if vals else None


def pick_first(records, field):
    for r in sorted(records, key=lambda x: SOURCE_PRIORITY.get(x.get("source"), 9)):
        if r.get(field):
            return r[field]
    return None


def pick_most_common(records, field):
    vals = [r.get(field) for r in records if r.get(field)]
    return Counter(vals).most_common(1)[0][0] if vals else None


# ---------- операции с БД ----------

def insert_product(cur, records):
    cur.execute(
        """INSERT INTO products (
            canonical_title, isbn13, description, publisher, year,
            language, pages, binding, age_restriction, category, image_url
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id""",
        (
            pick_title(records),
            records[0].get("isbn"),
            pick_longest(records, "description"),
            pick_most_common(records, "publisher"),
            pick_first(records, "year"),
            pick_first(records, "language"),
            pick_most_common(records, "pages"),
            pick_first(records, "binding"),
            pick_first(records, "age_restriction"),
            pick_most_common(records, "category"),
            pick_first(records, "image_url"),
        ),
    )
    return cur.fetchone()[0]


def insert_offers(cur, product_id, records):
    for r in records:
        cur.execute(
            """INSERT INTO offers (
                product_id, source, source_id, url, title,
                price, old_price, currency
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source, source_id) DO NOTHING""",
            (
                product_id, r["source"], r.get("source_id"), r["url"],
                r.get("title"), r.get("price"), r.get("old_price"),
                r.get("currency") or "RUB",
            ),
        )


def _upsert_author(cur, name):
    name = name[:500]  # защита от переполнения
    cur.execute("INSERT INTO authors (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (name,))
    cur.execute("SELECT id FROM authors WHERE name = %s", (name,))
    return cur.fetchone()[0]


def insert_authors(cur, product_id, records):
    seen = {}
    for r in records:
        for raw in r.get("authors") or []:
            for a in split_authors(raw):
                if not is_valid_author(a):
                    continue
                n = norm_author(a)
                if not n:
                    continue
                if n not in seen or len(a) > len(seen[n]):
                    seen[n] = a
    for position, display in enumerate(seen.values()):
        author_id = _upsert_author(cur, display)
        cur.execute(
            """INSERT INTO book_authors (product_id, author_id, position)
               VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
            (product_id, author_id, position),
        )


def insert_attributes(cur, product_id, records):
    merged = {}
    for r in records:
        for k, v in (r.get("extra") or {}).items():
            if k.lower() in REDUNDANT_ATTRS or not v:
                continue
            if k not in merged:
                merged[k] = v
    for k, v in merged.items():
        cur.execute(
            """INSERT INTO attributes (product_id, name, value)
               VALUES (%s, %s, %s) ON CONFLICT (product_id, name) DO NOTHING""",
            (product_id, k, v),
        )


def merge_extra(cur, product_id, record):
    """Добавляет авторов/атрибуты из нового offer к уже существующему product."""
    for raw in record.get("authors") or []:
        for a in split_authors(raw):
            if not is_valid_author(a):
                continue
            author_id = _upsert_author(cur, a)
            cur.execute(
                """INSERT INTO book_authors (product_id, author_id, position)
                   VALUES (%s, %s,
                           COALESCE((SELECT MAX(position)+1
                                     FROM book_authors WHERE product_id = %s), 0))
                   ON CONFLICT DO NOTHING""",
                (product_id, author_id, product_id),
            )
    for k, v in (record.get("extra") or {}).items():
        if k.lower() in REDUNDANT_ATTRS or not v:
            continue
        cur.execute(
            """INSERT INTO attributes (product_id, name, value)
               VALUES (%s, %s, %s) ON CONFLICT (product_id, name) DO NOTHING""",
            (product_id, k, v),
        )


# ---------- fuzzy ----------

def product_signature(records):
    title = norm_title(pick_title(records) or "")
    authors = " ".join(
        sorted({
            norm_author(a)
            for r in records
            for raw in (r.get("authors") or [])
            for a in split_authors(raw)
        })
    )
    return title, authors


def fuzzy_match(record, existing):
    title = norm_title(record.get("title") or "")
    if not title:
        return None
    authors = " ".join(sorted({
        norm_author(a)
        for raw in (record.get("authors") or [])
        for a in split_authors(raw)
    }))
    best_id, best_score = None, 0.0
    for pid, (p_title, p_authors) in existing.items():
        t_score = fuzz.token_set_ratio(title, p_title)
        if t_score < FUZZY_THRESHOLD:
            continue
        a_score = fuzz.token_set_ratio(authors, p_authors) if (authors and p_authors) else 100
        score = 0.75 * t_score + 0.25 * a_score
        if score > best_score:
            best_score, best_id = score, pid
    return (best_id, best_score) if best_score >= FUZZY_THRESHOLD else None


# ---------- main ----------

def main():
    with psycopg.connect(DB_URL) as conn:
        print("Truncating products (cascade)...")
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE products, authors, offers, book_authors, attributes "
                "RESTART IDENTITY CASCADE"
            )
        conn.commit()

        print("Reading raw_offers...")
        with conn.cursor() as cur:
            cur.execute("SELECT payload FROM raw_offers")
            records = [row[0] for row in cur.fetchall()]
        print(f"  {len(records)} raw records")

        groups = defaultdict(list)
        no_isbn = []
        for r in records:
            if r.get("isbn"):
                groups[r["isbn"]].append(r)
            else:
                no_isbn.append(r)
        print(f"  ISBN groups: {len(groups)}, without ISBN: {len(no_isbn)}")

        existing = {}  # product_id -> (norm_title, norm_authors)
        stats = {"products": 0, "offers": 0, "fuzzy_matched": 0, "fuzzy_new": 0}
        t0 = time.time()

        with conn.cursor() as cur:
            # 1) ISBN groups -> products + offers + authors + attributes
            for i, (isbn, recs) in enumerate(groups.items(), 1):
                pid = insert_product(cur, recs)
                insert_offers(cur, pid, recs)
                insert_authors(cur, pid, recs)
                insert_attributes(cur, pid, recs)
                existing[pid] = product_signature(recs)
                stats["products"] += 1
                stats["offers"] += len(recs)
                if i % 300 == 0:
                    print(f"  [{i}/{len(groups)}] products={stats['products']} "
                          f"offers={stats['offers']} elapsed={time.time()-t0:.0f}s")
            conn.commit()

            # 2) no-ISBN: fuzzy -> merge, else new product
            fuzzy_details = []
            for rec in no_isbn:
                m = fuzzy_match(rec, existing)
                if m:
                    pid, score = m
                    insert_offers(cur, pid, [rec])
                    merge_extra(cur, pid, rec)
                    stats["fuzzy_matched"] += 1
                    fuzzy_details.append((score, rec.get("title"), pid))
                else:
                    pid = insert_product(cur, [rec])
                    insert_offers(cur, pid, [rec])
                    insert_authors(cur, pid, [rec])
                    insert_attributes(cur, pid, [rec])
                    existing[pid] = product_signature([rec])
                    stats["products"] += 1
                    stats["fuzzy_new"] += 1
                    stats["offers"] += 1
            conn.commit()

            print(f"\nFuzzy matched records ({len(fuzzy_details)}):")
            for score, title, pid in sorted(fuzzy_details, reverse=True):
                print(f"  {score:5.1f}  product#{pid}  {title!r}")

        print("\n=== ИТОГО ===")
        for k, v in stats.items():
            print(f"  {k}: {v}")

        with conn.cursor() as cur:
            for table in ("products", "offers", "authors",
                          "book_authors", "attributes", "raw_offers"):
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                print(f"  {table}: {cur.fetchone()[0]} rows")


if __name__ == "__main__":
    main()
