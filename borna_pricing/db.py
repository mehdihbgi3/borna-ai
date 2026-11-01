from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY,
    key             TEXT NOT NULL UNIQUE,
    row_no          INTEGER NOT NULL,
    status          TEXT,
    name            TEXT NOT NULL,
    catalog_name    TEXT,
    part_no         TEXT,
    part_no_norm    TEXT,
    design          TEXT,
    ecu             TEXT,
    vehicle         TEXT,
    length_text     TEXT,
    length_mm       INTEGER,
    socket_color    TEXT,
    wire_color      TEXT,
    base_price_rial INTEGER NOT NULL CHECK (base_price_rial > 0),
    moq             INTEGER,
    alpha           TEXT,
    note            TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rates (
    id                 INTEGER PRIMARY KEY,
    fetched_at         TEXT NOT NULL,
    source             TEXT NOT NULL,
    slug               TEXT,
    field              TEXT,
    value_toman        INTEGER,
    source_updated_raw TEXT,
    source_updated_at  TEXT,
    status             TEXT NOT NULL,
    accepted           INTEGER NOT NULL DEFAULT 0,
    jump               TEXT,
    message            TEXT,
    raw                TEXT,
    http_status        INTEGER,
    reached            INTEGER NOT NULL DEFAULT 0,
    created_by         TEXT NOT NULL,
    decided_by         TEXT,
    decided_at         TEXT
);
CREATE INDEX IF NOT EXISTS ix_rates_accepted ON rates(accepted, id);

CREATE TABLE IF NOT EXISTS publications (
    id            INTEGER PRIMARY KEY,
    published_at  TEXT NOT NULL,
    kind          TEXT NOT NULL,
    run_id        INTEGER REFERENCES runs(id),
    rate_id       INTEGER REFERENCES rates(id),
    rate_rial     INTEGER NOT NULL,
    published_by  TEXT NOT NULL,
    approved_by   TEXT,
    note          TEXT,
    valid_until   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS published_prices (
    publication_id INTEGER NOT NULL REFERENCES publications(id),
    product_key    TEXT NOT NULL,
    row_no         INTEGER NOT NULL,
    name           TEXT NOT NULL,
    part_no        TEXT,
    vehicle        TEXT,
    length_text    TEXT,
    moq            INTEGER,
    price_rial     INTEGER NOT NULL,
    PRIMARY KEY (publication_id, product_key)
);

CREATE TABLE IF NOT EXISTS renewals (
    id             INTEGER PRIMARY KEY,
    publication_id INTEGER NOT NULL REFERENCES publications(id),
    run_id         INTEGER REFERENCES runs(id),
    rate_id        INTEGER REFERENCES rates(id),
    valid_until    TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    created_by     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id                     INTEGER PRIMARY KEY,
    created_at             TEXT NOT NULL,
    rate_id                INTEGER NOT NULL REFERENCES rates(id),
    rate_rial              INTEGER NOT NULL,
    base_rate_rial         INTEGER NOT NULL,
    ref_publication_id     INTEGER REFERENCES publications(id),
    day_ref_publication_id INTEGER REFERENCES publications(id),
    needs_publish          INTEGER NOT NULL,
    needs_approval         INTEGER NOT NULL,
    n_publish              INTEGER NOT NULL,
    n_approval             INTEGER NOT NULL,
    removed_keys           TEXT,
    status                 TEXT NOT NULL,
    params                 TEXT NOT NULL,
    created_by             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_prices (
    run_id           INTEGER NOT NULL REFERENCES runs(id),
    product_key      TEXT NOT NULL,
    base_price_rial  INTEGER NOT NULL,
    alpha            TEXT NOT NULL,
    exact_rial       TEXT NOT NULL,
    price_rial       INTEGER NOT NULL,
    pub_price_rial   INTEGER,
    change_vs_pub    TEXT,
    fx_effect_vs_pub TEXT,
    day_price_rial   INTEGER,
    change_vs_day    TEXT,
    fx_effect_vs_day TEXT,
    flag_publish     INTEGER NOT NULL,
    flag_approval    INTEGER NOT NULL,
    PRIMARY KEY (run_id, product_key)
);

CREATE TABLE IF NOT EXISTS users (
    id         INTEGER PRIMARY KEY,
    username   TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name  TEXT,
    role       TEXT NOT NULL CHECK (role IN ('viewer', 'approver', 'admin')),
    pw_hash    TEXT NOT NULL,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_login TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    id       INTEGER PRIMARY KEY,
    at       TEXT NOT NULL,
    username TEXT NOT NULL,
    action   TEXT NOT NULL,
    details  TEXT
);
CREATE INDEX IF NOT EXISTS ix_audit_at ON audit(at);
"""


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))


@contextmanager
def tx(conn: sqlite3.Connection):
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def audit(conn: sqlite3.Connection, at: str, username: str, action: str, details: dict | None = None) -> None:
    conn.execute("INSERT INTO audit(at, username, action, details) VALUES (?, ?, ?, ?)",
                 (at, username, action, json.dumps(details or {}, ensure_ascii=False, default=str)))
