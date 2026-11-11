#!/usr/bin/env python3
"""Парсер карточек книг с 3 сайтов. Читает links.jsonl, пишет books.jsonl."""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

DELAY_SEC = 1.0

# ---------- helpers ----------

def clean_text(s):
    if s is None:
        return None
    s = re.sub(r"\s+", " ", str(s)).strip()
    return s or None

def strip_html(s):
    """Удаляет HTML-теги, возвращает plain text."""
    if s is None:
        return None
    text = BeautifulSoup(str(s), "lxml").get_text(" ")
    return clean_text(text)

def parse_price(s):
    if s is None:
        return None
    s = re.sub(r"[^\d.,]", "", str(s)).replace(",", ".").rstrip(".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None

def parse_int(s):
    if s is None:
        return None
    m = re.search(r"\d+", str(s).replace(" ", "").replace("\u00a0", ""))
    return int(m.group(0)) if m else None

def normalize_isbn(s):
    """ISBN-10 -> ISBN-13, оставляем только цифры."""
    if not s:
        return None
    d = re.sub(r"[^0-9Xx]", "", str(s)).upper()
    if not d:
        return None
    if len(d) == 10:
        base = "978" + d[:9]
        total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(base))
        return base + str((10 - total % 10) % 10)
    return d

def extract_id_from_url(url, pattern):
    m = re.search(pattern, url)
    return m.group(1) if m else None

def meta_content(soup, *, name=None, prop=None, itemprop=None):
    attrs = {}
    if name: attrs["name"] = name
    if prop: attrs["property"] = prop
    if itemprop: attrs["itemprop"] = itemprop
    tag = soup.find("meta", attrs=attrs) if attrs else None
    return clean_text(tag.get("content")) if tag else None

def find_year(text):
    if not text:
        return None
    m = re.search(r"\b(19|20)\d{2}\b", str(text))
    return int(m.group(0)) if m else None

def fetch(session, url, retries=3):
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200:
                return r.text
            print(f"    HTTP {r.status_code} attempt {attempt}")
        except requests.RequestException as e:
            print(f"    {type(e).__name__}: {e} attempt {attempt}")
        time.sleep(DELAY_SEC * attempt * 2)
    return None

# ---------- парсеры по сайтам ----------

def parse_chitai_gorod(html, url):
    soup = BeautifulSoup(html, "lxml")
    book = None
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue
        if isinstance(data, dict):
            items = data.get("@graph", [data])
        elif isinstance(data, list):
            items = data
        else:
            items = []
        for item in items:
            if isinstance(item, dict) and item.get("@type") == "Book":
                book = item
                break
        if book:
            break
    if not book:
        return None

    af = book.get("author") or {}
    if isinstance(af, list):
        authors = [clean_text(a.get("name")) for a in af
                   if isinstance(a, dict) and a.get("name")]
    elif isinstance(af, dict) and af.get("name"):
        authors = [clean_text(af["name"])]
    else:
        authors = []

    offers = book.get("offers") or {}
    price = parse_price(offers.get("price")) if isinstance(offers, dict) else None
    currency = clean_text(offers.get("priceCurrency")) if isinstance(offers, dict) else "RUB"

    image = book.get("image") or {}
    image_url = image.get("url") if isinstance(image, dict) else (image if isinstance(image, str) else None)

    extra = {}
    for li in soup.select("li.product-properties-item"):
        label = li.select_one(".product-properties-item__title")
        content = li.select_one(".product-properties-item__content, .product-properties-item__value")
        if label and content:
            k = clean_text(label.get_text())
            v = clean_text(content.get_text())
            if k and v and k.lower() not in ("id товара", "isbn"):
                extra[k] = v

    old_price = None
    nbo = soup.select_one(".product-offer-nbo-old__price, .product-offer-price__old-text")
    if nbo:
        old_price = parse_price(nbo.get_text())

    pages = None
    for key in ("Кол-во стр.", "Страниц", "Количество страниц"):
        if key in extra:
            pages = parse_int(extra[key])
            if pages:
                break
    if not pages and book.get("numberOfPages"):
        pages = parse_int(book.get("numberOfPages"))

    year = None
    for key in ("Год издания", "Издательство"):
        if key in extra:
            y = find_year(extra[key])
            if y:
                year = y
                break

    publisher = clean_text(book.get("publisher"))
    if not publisher and extra.get("Издательство"):
        publisher = extra["Издательство"].split(",")[0].strip()

    return {
        "source": "chitai_gorod",
        "url": url,
        "source_id": extract_id_from_url(url, r"-(\d+)/?$"),
        "title": clean_text(book.get("name")),
        "authors": [a for a in authors if a],
        "publisher": publisher,
        "year": year,
        "isbn": normalize_isbn(book.get("isbn")),
        "price": price,
        "old_price": old_price,
        "currency": currency or "RUB",
        "description": strip_html(book.get("description")),
        "image_url": image_url,
        "category": clean_text(book.get("genre")),
        "pages": pages,
        "binding": extra.get("Переплёт"),
        "language": extra.get("Язык"),
        "age_restriction": clean_text(book.get("typicalAgeRange")),
        "extra": extra,
    }

def parse_labirint(html, url):
    soup = BeautifulSoup(html, "lxml")

    h1 = soup.select_one("h1[itemprop=name]")
    h1_text = clean_text(h1.get_text()) if h1 else None
    title = h1_text.split(":", 1)[0].strip() if h1_text and ":" in h1_text else h1_text

    authors = []
    for a in soup.select('a[href*="/authors/"]'):
        n = clean_text(a.get_text())
        if n and n not in authors:
            authors.append(n)
    authors = authors[:5]

    pub_tag = soup.select_one('a[href*="/pubhouse/"]')
    publisher_raw = clean_text(pub_tag.get_text()) if pub_tag else None

    year = None
    if pub_tag and pub_tag.parent:
        year = find_year(clean_text(pub_tag.parent.get_text()))

    isbn_raw = meta_content(soup, itemprop="isbn")
    price = parse_price(meta_content(soup, itemprop="price"))
    if price is None:
        p_el = soup.select_one("div.rubl, span.rubl")
        if p_el:
            price = parse_price(p_el.get_text())

    image_url = meta_content(soup, itemprop="image") or meta_content(soup, prop="og:image")
    if image_url and image_url.startswith("//"):
        image_url = "https:" + image_url

    description = None
    ann = soup.select_one("#annotation")
    if ann:
        for d in ann.find_all("div"):
            if d.find("div"):
                continue
            t = clean_text(d.get_text())
            if t and len(t) > 80:
                description = t
                break

    category = None
    cat_links = soup.select('a[href*="/genres/"]')
    if cat_links:
        category = clean_text(cat_links[-1].get_text())

    extra = {}
    for el in soup.select('[class*="_feature_"]'):
        name_el = el.select_one('[class*="_name_"]')
        if not name_el:
            continue
        k = clean_text(name_el.get_text())
        value_div = name_el.find_next_sibling()
        if value_div:
            v = clean_text(value_div.get_text())
            if k and v and k.lower() not in ("id товара", "isbn", "автор"):
                extra[k] = v

    pages = None
    for key in ("Страниц", "Кол-во стр.", "Количество страниц"):
        if key in extra:
            pages = parse_int(extra[key])
            if pages:
                break

    publisher_clean = None
    if publisher_raw:
        publisher_clean = publisher_raw.split(",")[0].strip()

    return {
        "source": "labirint",
        "url": url,
        "source_id": extract_id_from_url(url, r"/books/(\d+)/?$"),
        "title": title,
        "authors": authors,
        "publisher": publisher_clean,
        "year": year,
        "isbn": normalize_isbn(isbn_raw),
        "price": price,
        "old_price": None,
        "currency": "RUB",
        "description": description,
        "image_url": image_url,
        "category": category,
        "pages": pages,
        "binding": extra.get("Тип обложки"),
        "language": extra.get("Язык"),
        "age_restriction": None,
        "extra": extra,
    }

def parse_book24(html, url):
    soup = BeautifulSoup(html, "lxml")

    h1 = soup.select_one("h1[itemprop=name]")
    h1_text = clean_text(h1.get_text()) if h1 else None
    title = h1_text.split(":", 1)[1].strip() if h1_text and ":" in h1_text else h1_text

    authors = []
    for span in soup.select('span[itemprop=author]'):
        meta = span.find("meta", attrs={"itemprop": "name"})
        name = clean_text(meta.get("content")) if meta else None
        if name and name not in authors:
            authors.append(name)

    isbn_raw = meta_content(soup, itemprop="isbn")
    price = parse_price(meta_content(soup, itemprop="price"))
    if price is None:
        el = soup.select_one(".product-sidebar-price__price")
        if el:
            price = parse_price(el.get_text())
    old_price = None
    el = soup.select_one(".product-sidebar-price__price-old")
    if el:
        old_price = parse_price(el.get_text())

    image_url = meta_content(soup, itemprop="image")
    if image_url and image_url.startswith("//"):
        image_url = "https:" + image_url

    description = None
    desc_el = soup.select_one("#product-about .product-about__text")
    if desc_el:
        description = strip_html(desc_el.get_text())

    category = meta_content(soup, itemprop="genre")
    publisher = meta_content(soup, itemprop="publisher")

    pages = parse_int(meta_content(soup, itemprop="numberOfPages"))

    extra = {}
    for item in soup.select(".product-characteristic__item"):
        label = item.select_one(".product-characteristic__label")
        value = item.select_one(".product-characteristic__value")
        if label and value:
            k = clean_text(label.get_text()).rstrip(":")
            v = clean_text(value.get_text())
            if k and v and k.lower() not in ("isbn",):
                extra[k] = v

    if not pages:
        for key in ("Количество страниц", "Страниц", "Кол-во стр."):
            if key in extra:
                pages = parse_int(extra[key])
                if pages:
                    break

    return {
        "source": "book24",
        "url": url,
        "source_id": extract_id_from_url(url, r"-(\d+)/?$"),
        "title": title,
        "authors": authors,
        "publisher": publisher,
        "year": None,
        "isbn": normalize_isbn(isbn_raw),
        "price": price,
        "old_price": old_price,
        "currency": "RUB",
        "description": description,
        "image_url": image_url,
        "category": category,
        "pages": pages,
        "binding": extra.get("Переплет"),
        "language": None,
        "age_restriction": extra.get("Возрастное ограничение"),
        "extra": extra,
    }

PARSERS = {
    "chitai_gorod": parse_chitai_gorod,
    "labirint": parse_labirint,
    "book24": parse_book24,
}

# ---------- оркестрация ----------

def load_done_urls(path):
    done = set()
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["url"])
                except Exception:
                    pass
    return done

def run(site, limit=None):
    parser = PARSERS[site]
    raw_dir = Path("data/raw") / site
    links_file = raw_dir / "links.jsonl"
    out_file = raw_dir / "books.jsonl"

    if not links_file.exists():
        print(f"[{site}] нет {links_file}")
        return

    with links_file.open("r", encoding="utf-8") as f:
        all_links = [json.loads(line)["url"] for line in f if line.strip()]

    done = load_done_urls(out_file)
    todo = [u for u in all_links if u not in done]
    print(f"[{site}] ссылок {len(all_links)}, уже собрано {len(done)}, "
          f"к парсингу {len(todo)}")

    if limit:
        todo = todo[:limit]

    session = requests.Session()
    session.headers.update(HEADERS)

    ok = fail = 0
    start = time.time()

    with out_file.open("a", encoding="utf-8") as fout:
        for i, url in enumerate(todo, 1):
            html = fetch(session, url)
            if not html:
                fail += 1
                print(f"  [{i}/{len(todo)}] FAIL {url}")
                time.sleep(DELAY_SEC)
                continue
            try:
                rec = parser(html, url)
            except Exception as e:
                fail += 1
                print(f"  [{i}/{len(todo)}] PARSE_ERR {url}: {type(e).__name__}: {e}")
                time.sleep(DELAY_SEC)
                continue
            if not rec or not rec.get("title"):
                fail += 1
                print(f"  [{i}/{len(todo)}] EMPTY {url}")
                time.sleep(DELAY_SEC)
                continue
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fout.flush()
            ok += 1
            if i % 20 == 0 or i == len(todo):
                el = time.time() - start
                print(f"  [{i}/{len(todo)}] ok={ok} fail={fail} "
                      f"({i/el:.1f} c-1, {el:.0f}s)")
            time.sleep(DELAY_SEC)

    print(f"[{site}] ГОТОВО: ok={ok}, fail={fail}, файл {out_file}")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("site", choices=[*PARSERS, "all"])
    p.add_argument("--limit", type=int, default=None,
                   help="Сколько карточек парсить в этом прогоне")
    args = p.parse_args()

    if args.site == "all":
        for key in PARSERS:
            print(f"\n=== {key} ===")
            try:
                run(key, args.limit)
            except KeyboardInterrupt:
                print("Прервано")
                break
            except Exception as e:
                print(f"[{key}] ОШИБКА: {e}", file=sys.stderr)
    else:
        run(args.site, args.limit)
