from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

BASE_VERSION = 1
SCHEMA_VERSION = 2

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


MARKET_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS agent_runs (
        id          INTEGER PRIMARY KEY,
        agent       TEXT NOT NULL,
        trigger     TEXT NOT NULL,
        started_at  TEXT NOT NULL,
        finished_at TEXT,
        status      TEXT NOT NULL CHECK (status IN ('running', 'ok', 'partial', 'failed', 'skipped')),
        stats       TEXT,
        message     TEXT,
        created_by  TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ix_agent_runs ON agent_runs(agent, id)",
    """CREATE TABLE IF NOT EXISTS sources (
        id              INTEGER PRIMARY KEY,
        key             TEXT NOT NULL UNIQUE,
        name            TEXT NOT NULL,
        kind            TEXT NOT NULL CHECK (kind IN ('marketplace', 'aggregator', 'shop', 'manufacturer',
                                                      'channel', 'manual')),
        domain          TEXT,
        start_urls      TEXT NOT NULL DEFAULT '[]',
        unit            TEXT NOT NULL CHECK (unit IN ('toman', 'rial')),
        price_level     TEXT NOT NULL CHECK (price_level IN ('retail', 'wholesale', 'unknown')),
        parser          TEXT NOT NULL DEFAULT 'auto' CHECK (parser IN ('auto', 'regex')),
        item_regex      TEXT,
        interval_hours  INTEGER NOT NULL DEFAULT 24 CHECK (interval_hours >= 1),
        delay_seconds   INTEGER NOT NULL DEFAULT 5 CHECK (delay_seconds >= 0),
        max_pages       INTEGER NOT NULL DEFAULT 10 CHECK (max_pages >= 1),
        policy_status   TEXT NOT NULL DEFAULT 'pending' CHECK (policy_status IN ('pending', 'approved', 'blocked')),
        policy_note     TEXT,
        policy_by       TEXT,
        policy_at       TEXT,
        active          INTEGER NOT NULL DEFAULT 1,
        note            TEXT,
        created_at      TEXT NOT NULL,
        created_by      TEXT NOT NULL,
        updated_at      TEXT NOT NULL,
        last_run_at     TEXT,
        last_success_at TEXT,
        failure_count   INTEGER NOT NULL DEFAULT 0,
        last_error      TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS snapshots (
        id             INTEGER PRIMARY KEY,
        source_id      INTEGER NOT NULL REFERENCES sources(id),
        kind           TEXT NOT NULL CHECK (kind IN ('page', 'upload', 'note')),
        url            TEXT,
        fetched_at     TEXT NOT NULL,
        http_status    INTEGER,
        content_type   TEXT,
        bytes          INTEGER NOT NULL,
        sha256         TEXT NOT NULL,
        path           TEXT NOT NULL,
        filename       TEXT,
        run_id         INTEGER REFERENCES agent_runs(id),
        parser_version TEXT,
        n_items        INTEGER,
        message        TEXT,
        created_by     TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ix_snapshots_source ON snapshots(source_id, id)",
    """CREATE TABLE IF NOT EXISTS external_products (
        id            INTEGER PRIMARY KEY,
        source_id     INTEGER NOT NULL REFERENCES sources(id),
        ext_key       TEXT NOT NULL,
        url           TEXT,
        title         TEXT NOT NULL,
        seller        TEXT,
        brand         TEXT,
        attrs         TEXT NOT NULL DEFAULT '{}',
        tier          TEXT NOT NULL DEFAULT 'unknown' CHECK (tier IN ('genuine', 'oem', 'aftermarket', 'unknown')),
        tier_by       TEXT,
        is_own        INTEGER NOT NULL DEFAULT 0,
        match_state   TEXT NOT NULL DEFAULT 'new' CHECK (match_state IN ('new', 'proposed', 'matched', 'unmatched')),
        first_seen_at TEXT NOT NULL,
        last_seen_at  TEXT NOT NULL,
        UNIQUE (source_id, ext_key)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_ext_state ON external_products(match_state)",
    """CREATE TABLE IF NOT EXISTS observations (
        id                  INTEGER PRIMARY KEY,
        external_product_id INTEGER NOT NULL REFERENCES external_products(id),
        snapshot_id         INTEGER NOT NULL REFERENCES snapshots(id),
        observed_at         TEXT NOT NULL,
        kind                TEXT NOT NULL CHECK (kind IN ('price', 'quote_only')),
        availability        TEXT NOT NULL CHECK (availability IN ('in_stock', 'out_of_stock', 'unknown')),
        price_rial          INTEGER CHECK (price_rial IS NULL OR price_rial > 0),
        list_price_rial     INTEGER CHECK (list_price_rial IS NULL OR list_price_rial > 0),
        raw_price           TEXT,
        unit                TEXT NOT NULL CHECK (unit IN ('toman', 'rial')),
        price_level         TEXT NOT NULL CHECK (price_level IN ('retail', 'wholesale', 'unknown')),
        min_qty             INTEGER CHECK (min_qty IS NULL OR min_qty > 0),
        rate_id             INTEGER REFERENCES rates(id),
        status              TEXT NOT NULL CHECK (status IN ('ok', 'pending', 'quarantined', 'rejected')),
        status_note         TEXT,
        status_by           TEXT,
        status_at           TEXT,
        entry               TEXT NOT NULL CHECK (entry IN ('crawl', 'manual')),
        created_by          TEXT NOT NULL,
        created_at          TEXT NOT NULL,
        CHECK ((kind = 'price' AND price_rial IS NOT NULL) OR (kind = 'quote_only' AND price_rial IS NULL)),
        UNIQUE (external_product_id, snapshot_id)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_obs_ext_time ON observations(external_product_id, observed_at)",
    "CREATE INDEX IF NOT EXISTS ix_obs_status ON observations(status)",
    """CREATE TABLE IF NOT EXISTS matches (
        id                  INTEGER PRIMARY KEY,
        external_product_id INTEGER NOT NULL REFERENCES external_products(id),
        product_key         TEXT NOT NULL,
        status              TEXT NOT NULL CHECK (status IN ('proposed', 'approved', 'rejected')),
        verdict             TEXT NOT NULL CHECK (verdict IN ('strong', 'candidate', 'review', 'reject', 'manual')),
        relation            TEXT NOT NULL DEFAULT 'equivalent' CHECK (relation IN ('equivalent', 'substitute')),
        reasons             TEXT NOT NULL DEFAULT '[]',
        score               INTEGER NOT NULL DEFAULT 0,
        engine_version      TEXT NOT NULL,
        proposed_at         TEXT NOT NULL,
        decided_by          TEXT,
        decided_at          TEXT,
        note                TEXT,
        UNIQUE (external_product_id, product_key)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_matches_status ON matches(status, verdict)",
    "CREATE INDEX IF NOT EXISTS ix_matches_product ON matches(product_key, status)",
    """CREATE TABLE IF NOT EXISTS product_specs (
        product_key TEXT NOT NULL,
        attr        TEXT NOT NULL,
        value       TEXT NOT NULL,
        note        TEXT,
        updated_by  TEXT NOT NULL,
        updated_at  TEXT NOT NULL,
        PRIMARY KEY (product_key, attr, value)
    )""",
    """CREATE TABLE IF NOT EXISTS reports (
        id           INTEGER PRIMARY KEY,
        kind         TEXT NOT NULL,
        created_at   TEXT NOT NULL,
        period_start TEXT NOT NULL,
        period_end   TEXT NOT NULL,
        run_id       INTEGER REFERENCES agent_runs(id),
        data         TEXT NOT NULL,
        created_by   TEXT NOT NULL
    )""",
]

MIGRATIONS: dict[int, list[str]] = {2: MARKET_SCHEMA}


def schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row and str(row[0]).isdigit() else 0


def pending_migrations(conn: sqlite3.Connection) -> list[int]:
    current = schema_version(conn)
    return [v for v in sorted(MIGRATIONS) if current < v <= SCHEMA_VERSION]


def migrate(conn: sqlite3.Connection) -> list[int]:
    done = []
    for version in pending_migrations(conn):
        with tx(conn):
            for stmt in MIGRATIONS[version]:
                conn.execute(stmt)
            conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (str(version),))
        done.append(version)
    return done


def init_schema(conn: sqlite3.Connection) -> list[int]:
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(BASE_VERSION),))
    return migrate(conn)


def meta_get(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def meta_set(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    if value is None:
        conn.execute("DELETE FROM meta WHERE key=?", (key,))
    else:
        conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, value))
