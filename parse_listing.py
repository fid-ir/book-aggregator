#!/usr/bin/env python3
"""Собирает ссылки на карточки товаров с листингов 3 книжных сайтов."""
import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

DELAY_SEC = 1.5
MAX_PAGES = 200
STOP_AFTER_EMPTY = 2

SITES = {
    "chitai_gorod": {
        "base": "https://www.chitai-gorod.ru",
        "page_url": lambda n: (
            "https://www.chitai-gorod.ru/catalog/books-18030"
            + ("" if n == 1 else f"?page={n}")
        ),
        "link_re": re.compile(r"^/product/[a-z0-9\-]+-\d+/?$", re.IGNORECASE),
    },
    "labirint": {
        "base": "https://www.labirint.ru",
        "page_url": lambda n: (
            "https://www.labirint.ru/books/"
            + ("" if n == 1 else f"?page={n}")
        ),
        "link_re": re.compile(r"^/books/\d+/?$"),
    },
    "book24": {
        "base": "https://book24.ru",
        "page_url": lambda n: (
            "https://book24.ru/catalog/" if n == 1
            else f"https://book24.ru/catalog/page-{n}/"
        ),
        "link_re": re.compile(r"^/product/[a-z0-9\-]+-\d+/?$", re.IGNORECASE),
    },
}


def fetch(session, url, retries=3):
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, timeout=30)
            if r.status_code == 200:
                return r.text
            print(f"    HTTP {r.status_code}, попытка {attempt}")
        except requests.RequestException as e:
            print(f"    {type(e).__name__}: {e}, попытка {attempt}")
        time.sleep(DELAY_SEC * attempt)
    return None


def extract_links(html, base, link_re):
    soup = BeautifulSoup(html, "lxml")
    links = set()
    for a in soup.find_all("a", href=True):
        absolute = urljoin(base + "/", a["href"])
        path = urlsplit(absolute).path
        if link_re.match(path):
            links.add(base + path.rstrip("/"))
    return links


def run(site, max_items=None, save_html=False):
    cfg = SITES[site]
    out_dir = Path("data/raw") / site
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "links.jsonl"
    html_dir = out_dir / "html"
    if save_html:
        html_dir.mkdir(exist_ok=True)

    session = requests.Session()
    session.headers.update(HEADERS)

    all_links = set()
    empty_streak = 0

    for n in range(1, MAX_PAGES + 1):
        url = cfg["page_url"](n)
        print(f"[{site}] стр. {n}: {url}")
        html = fetch(session, url)
        if html is None:
            print("    пропуск (не удалось скачать)")
            time.sleep(DELAY_SEC)
            continue

        if save_html and n <= 3:
            (html_dir / f"page_{n:04d}.html").write_text(html, encoding="utf-8")

        links = extract_links(html, cfg["base"], cfg["link_re"])
        new_links = links - all_links
        print(f"    найдено {len(links)}, новых {len(new_links)}, "
              f"всего {len(all_links) + len(new_links)}")

        if not new_links:
            empty_streak += 1
            if empty_streak >= STOP_AFTER_EMPTY:
                print(f"    {STOP_AFTER_EMPTY} пустых страниц подряд — стоп")
                break
        else:
            empty_streak = 0

        all_links.update(links)

        if max_items and len(all_links) >= max_items:
            print(f"    лимит {max_items} — стоп")
            break

        time.sleep(DELAY_SEC)

    with out_file.open("w", encoding="utf-8") as f:
        for link in sorted(all_links):
            f.write(json.dumps({"source": site, "url": link},
                              ensure_ascii=False) + "\n")

    print(f"[{site}] итого {len(all_links)} ссылок -> {out_file}")
    return len(all_links)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("site", choices=[*SITES, "all"])
    p.add_argument("--max-items", type=int, default=None)
    p.add_argument("--save-html", action="store_true")
    args = p.parse_args()

    if args.site == "all":
        for key in SITES:
            try:
                run(key, args.max_items, args.save_html)
            except Exception as e:
                print(f"[{key}] ОШИБКА: {e}", file=sys.stderr)
    else:
        run(args.site, args.max_items, args.save_html)
