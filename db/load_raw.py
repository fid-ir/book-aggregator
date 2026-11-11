#!/usr/bin/env python3
"""Load JSONL files into raw_offers staging table. Idempotent."""
import json
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

DB_URL = "postgresql://book:book_pass@localhost:5432/book_aggregator"
RAW_DIR = Path("data/raw")


def load(conn, path):
    loaded = skipped = 0
    with path.open("r", encoding="utf-8") as f, conn.cursor() as cur:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if not rec.get("url") or not rec.get("source"):
                continue
            cur.execute(
                """INSERT INTO raw_offers (source, source_id, url, payload)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (url) DO NOTHING""",
                (rec["source"], rec.get("source_id"), rec["url"], Jsonb(rec)),
            )
            if cur.rowcount > 0:
                loaded += 1
            else:
                skipped += 1
        conn.commit()
    return loaded, skipped


def main():
    with psycopg.connect(DB_URL) as conn:
        total_loaded = total_skipped = 0
        for site_dir in sorted(RAW_DIR.iterdir()):
            if not site_dir.is_dir():
                continue
            path = site_dir / "books.jsonl"
            if not path.exists():
                print(f"skip {path}: no books.jsonl")
                continue
            loaded, skipped = load(conn, path)
            print(f"{site_dir.name}: loaded={loaded}, skipped={skipped}")
            total_loaded += loaded
            total_skipped += skipped
        print(f"\nTOTAL: loaded={total_loaded}, skipped={total_skipped}")

        with conn.cursor() as cur:
            cur.execute("SELECT source, COUNT(*) FROM raw_offers GROUP BY source ORDER BY source")
            for source, cnt in cur.fetchall():
                print(f"  raw_offers[{source}]: {cnt}")


if __name__ == "__main__":
    main()
