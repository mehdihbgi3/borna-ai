import sqlite3
from datetime import datetime

import pytest

from borna_pricing import clock, config, db
from borna_pricing.clock import TEHRAN
from borna_pricing.service import Service

from conftest import write_settings

MARKET_TABLES = {"agent_runs", "sources", "snapshots", "external_products", "observations", "matches",
                 "product_specs", "reports"}


def tables(path):
    con = sqlite3.connect(str(path))
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()


def test_fresh_install_is_at_latest_version(tmp_path):
    cfg = config.load_config(write_settings(tmp_path))
    s = Service(cfg)
    clock.set_fixed_now(datetime(2026, 10, 31, 13, 30, tzinfo=TEHRAN))
    s.init()
    with s.db() as c:
        assert db.schema_version(c) == db.SCHEMA_VERSION == 2
        assert db.pending_migrations(c) == []
    assert MARKET_TABLES <= tables(cfg.paths.database)
    assert s.ensure_schema() == []


def test_upgrade_from_v1_takes_backup_first(tmp_path):
    cfg = config.load_config(write_settings(tmp_path))
    clock.set_fixed_now(datetime(2026, 10, 31, 13, 30, tzinfo=TEHRAN))
    con = db.connect(cfg.paths.database)
    con.executescript(db.SCHEMA)
    con.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '1')")
    con.execute("INSERT INTO audit(at, username, action, details) VALUES ('2026-10-30T10:00:00+00:00', 'x', 'login', '{}')")
    con.close()
    s = Service(cfg)
    assert s.ensure_schema() == [2]
    assert MARKET_TABLES <= tables(cfg.paths.database)
    backups = list((cfg.paths.database.parent / "backups").glob("pre_v2_*.sqlite3"))
    assert len(backups) == 1
    old = tables(backups[0])
    assert "audit" in old and not (MARKET_TABLES & old)
    with s.db() as c:
        assert c.execute("SELECT COUNT(*) FROM audit WHERE action='login'").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM audit WHERE action='schema_migrate'").fetchone()[0] == 1
    assert s.ensure_schema() == []
    s.init()
    assert len(s.products()) == 27


def test_migration_is_atomic(tmp_path, monkeypatch):
    cfg = config.load_config(write_settings(tmp_path))
    con = db.connect(cfg.paths.database)
    con.executescript(db.SCHEMA)
    con.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '1')")
    monkeypatch.setitem(db.MIGRATIONS, 2, db.MARKET_SCHEMA[:3] + ["CREATE TABLE broken ("])
    with pytest.raises(sqlite3.OperationalError):
        db.migrate(con)
    assert db.schema_version(con) == 1
    assert "agent_runs" not in {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()


def test_market_settings_defaults_and_validation(tmp_path):
    cfg = config.load_config(write_settings(tmp_path))
    assert cfg.market.own_sellers == ("برنا سنسور",)
    assert cfg.market.min_price_usd == 2 and cfg.market.max_price_usd == 60 and cfg.market.stale_days == 14
    assert cfg.collector.min_delay_seconds == 3 and cfg.agents.enabled
    assert cfg.agents.report_weekday == 5 and cfg.paths.evidence_dir.name == "evidence"
    bad = write_settings(tmp_path, "[market]\nmin_price_usd = 9\nmax_price_usd = 5\n")
    with pytest.raises(config.ConfigError):
        config.load_config(bad)
    bad = write_settings(tmp_path, "[agents]\nreport_weekday = someday\n")
    with pytest.raises(config.ConfigError):
        config.load_config(bad)
    ok = write_settings(tmp_path, "[market]\nown_sellers = برنا سنسور، برنا سنسور اکسیژن فارس\n")
    assert config.load_config(ok).market.own_sellers == ("برنا سنسور", "برنا سنسور اکسیژن فارس")
