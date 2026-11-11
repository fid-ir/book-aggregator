-- ============================================================
-- Book Aggregator DB Schema (PostgreSQL 15+)
-- ============================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------- staging: сырые данные 1:1 с JSONL ----------
CREATE TABLE IF NOT EXISTS raw_offers (
    id          BIGSERIAL PRIMARY KEY,
    source      VARCHAR(50)  NOT NULL,
    source_id   VARCHAR(100),
    url         TEXT         NOT NULL,
    payload     JSONB        NOT NULL,
    loaded_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (url)
);
CREATE INDEX IF NOT EXISTS idx_raw_offers_source ON raw_offers(source);

-- ---------- products: уникальные книги ----------
CREATE TABLE IF NOT EXISTS products (
    id                BIGSERIAL PRIMARY KEY,
    canonical_title   TEXT         NOT NULL,
    isbn13            VARCHAR(13)  UNIQUE,
    description       TEXT,
    publisher         VARCHAR(500),
    year              INT CHECK (year IS NULL OR (year BETWEEN 1400 AND 2100)),
    language          VARCHAR(50),
    pages             INT CHECK (pages IS NULL OR pages > 0),
    binding           VARCHAR(500),
    age_restriction   VARCHAR(100),
    category          VARCHAR(500),
    image_url         TEXT,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_products_isbn   ON products(isbn13);
CREATE INDEX IF NOT EXISTS idx_products_title  ON products USING GIN (canonical_title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_products_fts    ON products
    USING GIN (to_tsvector('russian',
        COALESCE(canonical_title, '') || ' ' || COALESCE(description, '')));

-- ---------- offers: предложения с сайтов ----------
CREATE TABLE IF NOT EXISTS offers (
    id          BIGSERIAL PRIMARY KEY,
    product_id  BIGINT      NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    source      VARCHAR(50) NOT NULL,
    source_id   VARCHAR(100) NOT NULL,
    url         TEXT        NOT NULL,
    title       TEXT        NOT NULL,
    price       NUMERIC(10,2) CHECK (price IS NULL OR price >= 0),
    old_price   NUMERIC(10,2) CHECK (old_price IS NULL OR old_price >= 0),
    currency    VARCHAR(3)  NOT NULL DEFAULT 'RUB',
    parsed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_offers_product ON offers(product_id);
CREATE INDEX IF NOT EXISTS idx_offers_source  ON offers(source);
CREATE INDEX IF NOT EXISTS idx_offers_price   ON offers(price) WHERE price IS NOT NULL;

-- ---------- authors ----------
CREATE TABLE IF NOT EXISTS authors (
    id    BIGSERIAL PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_authors_name_trgm
    ON authors USING GIN (name gin_trgm_ops);

-- ---------- book_authors (M2M) ----------
CREATE TABLE IF NOT EXISTS book_authors (
    product_id  BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    author_id   BIGINT NOT NULL REFERENCES authors(id)  ON DELETE CASCADE,
    position    INT    NOT NULL DEFAULT 0,
    PRIMARY KEY (product_id, author_id)
);
CREATE INDEX IF NOT EXISTS idx_book_authors_author ON book_authors(author_id);

-- ---------- attributes: доп. характеристики ----------
CREATE TABLE IF NOT EXISTS attributes (
    id          BIGSERIAL PRIMARY KEY,
    product_id  BIGINT       NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    name        VARCHAR(255) NOT NULL,
    value       TEXT,
    UNIQUE (product_id, name)
);
CREATE INDEX IF NOT EXISTS idx_attributes_product ON attributes(product_id);
CREATE INDEX IF NOT EXISTS idx_attributes_name    ON attributes(name);
