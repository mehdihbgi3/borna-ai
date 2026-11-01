import shutil
from dataclasses import replace
from datetime import datetime

import pytest
from openpyxl import load_workbook

from borna_pricing import clock, config, fx
from borna_pricing.clock import TEHRAN, parse_iso
from borna_pricing.service import Service, ServiceError

from conftest import FIXTURES, write_settings


def at(y, m, d, hh, mm):
    clock.set_fixed_now(datetime(y, m, d, hh, mm, tzinfo=TEHRAN))


@pytest.fixture
def svc(tmp_path):
    def make(extra=""):
        s = Service(config.load_config(write_settings(tmp_path, extra)))
        at(2026, 10, 31, 13, 30)
        s.init()
        return s
    return make


def test_init_imports_27_products_and_base_list(svc):
    s = svc()
    prods = s.products()
    assert len(prods) == 27 and len({p["key"] for p in prods}) == 27
    ef7 = next(p for p in prods if p["key"] == "FIB206-350")
    assert ef7["base_price_rial"] == 34500000 and ef7["part_no_norm"] == "0258007342"
    assert next(p for p in prods if p["key"] == "FIB101-480")["length_mm"] == 480
    pub = s.publication()
    assert pub["kind"] == "base" and len(pub["rows"]) == 27 and pub["rate_rial"] == 2600000
    assert parse_iso(pub["published_at"]).astimezone(TEHRAN) == datetime(2026, 10, 12, 9, 0, tzinfo=TEHRAN)
    assert not pub["is_valid"]
    s.init()
    assert len(s.publications()) == 1 and len(s.products()) == 27


def test_a_full_pilot_day(svc):
    s = svc()
    at(2026, 10, 31, 13, 33)
    r = s.fetch_now("system")
    assert r["status"] == "held" and r["value_toman"] == 273400 and r["run_id"] is None
    st = s.state()
    assert st["held"]["id"] == r["rate_id"] and st["last_accepted"] is None
    run_id = s.decide_held(r["rate_id"], "finance", accept=True)["run_id"]
    st = s.state()
    run = st["run"]
    assert run["id"] == run_id and run["needs_publish"] and run["needs_approval"] and run["n_publish"] == 27
    prices = {x["product_key"]: x["price_rial"] for x in run["rows"]}
    assert prices["TMP-01"] == 23000000 and prices["FIB206-350"] == 36500000 and prices["FIB201-400"] == 24500000
    with pytest.raises(ServiceError):
        s.publish(run_id, "finance")
    pid = s.publish(run_id, "finance", confirm_approval=True, note="نخستین انتشار")
    pub = s.publication()
    assert pub["id"] == pid and pub["kind"] == "approved" and pub["approved_by"] == "finance"
    assert pub["is_valid"] and pub["valid_until_eff"] == datetime(2026, 10, 31, 17, 0, tzinfo=TEHRAN)
    assert {x["product_key"]: x["price_rial"] for x in pub["rows"]} == prices

    at(2026, 10, 31, 14, 0)
    with pytest.raises(ServiceError) as e:
        s.fetch_now("finance")
    assert "۱۴:۳۳" in str(e.value)
    assert s.scheduled_fetch() is None
    at(2026, 10, 31, 14, 34)
    r2 = s.scheduled_fetch()
    assert r2["status"] == "ok"
    assert s.state()["run"]["status"] == "no_change"

    at(2026, 11, 1, 9, 10)
    assert s.state()["publication"]["is_valid"] is False
    r3 = s.scheduled_fetch()
    assert r3["status"] == "stale"
    s.manual_rate(274000, "finance", "میانگین سه صرافی")
    st = s.state()
    assert st["run"]["status"] == "no_change" and st["publication"]["is_valid"]
    assert st["publication"]["valid_until_eff"] == datetime(2026, 11, 1, 17, 0, tzinfo=TEHRAN)

    at(2026, 11, 1, 11, 0)
    s.manual_rate(281000, "finance", "صرافی")
    run = s.state()["run"]
    assert run["needs_publish"] and not run["needs_approval"]
    s.publish(run["id"], "finance")
    at(2026, 11, 1, 15, 0)
    s.manual_rate(289200, "finance", "صرافی")
    run = s.state()["run"]
    assert run["needs_publish"] and run["needs_approval"]
    kinds = [p["kind"] for p in s.publications()]
    assert kinds == ["normal", "approved", "base"]


def test_rejecting_and_superseding_held_rates(svc):
    s = svc()
    at(2026, 10, 31, 13, 33)
    first = s.fetch_now("system")
    assert first["status"] == "held"
    s.decide_held(first["rate_id"], "ceo", accept=False, note="عدد عجیب است")
    assert s.state()["held"] is None and s.state()["last_accepted"] is None
    with pytest.raises(ServiceError):
        s.decide_held(first["rate_id"], "ceo", accept=True)
    at(2026, 10, 31, 14, 40)
    second = s.fetch_now("system")
    assert second["status"] == "held"
    s.manual_rate(270000, "ceo", "نرخ صرافی ملی")
    assert s.state()["held"] is None
    with pytest.raises(ServiceError):
        s.decide_held(second["rate_id"], "ceo", accept=True)


def test_old_held_rate_cannot_be_accepted(svc):
    s = svc()
    at(2026, 10, 31, 13, 33)
    r = s.fetch_now("system")
    at(2026, 10, 31, 16, 30)
    with pytest.raises(ServiceError) as e:
        s.decide_held(r["rate_id"], "finance", accept=True)
    assert "کهنه" in str(e.value)


def test_only_the_latest_run_can_be_published(svc):
    s = svc()
    at(2026, 10, 31, 10, 0)
    s.manual_rate(273400, "finance", "x")
    old = s.state()["run"]["id"]
    s.recompute("finance")
    with pytest.raises(ServiceError) as e:
        s.publish(old, "finance", confirm_approval=True)
    assert "جدیدتر" in str(e.value)
    new = s.state()["run"]["id"]
    s.publish(new, "finance", confirm_approval=True)
    with pytest.raises(ServiceError):
        s.publish(new, "finance", confirm_approval=True)


def test_auto_publish_when_enabled(svc):
    s = svc("[pricing]\nauto_publish = true")
    at(2026, 10, 31, 10, 0)
    s.manual_rate(268000, "finance", "x")
    assert s.publication()["kind"] == "auto"
    s.manual_rate(290000, "finance", "x")
    assert s.publication()["kind"] == "auto" and s.state()["run"]["needs_approval"]


def test_manual_rate_validation(svc):
    s = svc()
    with pytest.raises(ServiceError):
        s.manual_rate(2734000, "finance", "ریال وارد شد")
    with pytest.raises(ServiceError):
        s.manual_rate(273400, "finance", "  ")


def test_schedule_respects_working_hours_and_retry(svc, tmp_path):
    s = svc()
    at(2026, 11, 6, 10, 0)
    assert s.next_auto_fetch_at() == datetime(2026, 11, 7, 9, 0, tzinfo=TEHRAN)
    assert s.scheduled_fetch() is None
    at(2026, 11, 5, 12, 30)
    s.fetch_now("system")
    assert s.next_auto_fetch_at() == datetime(2026, 11, 7, 9, 0, tzinfo=TEHRAN)
    cfg = config.load_config(tmp_path / "settings.ini")
    live = Service(replace(cfg, source=replace(cfg.source, mode="live")))
    at(2026, 11, 7, 9, 0)
    r = live.fetch_now("system", force=True)
    assert r["status"] == "error" and "توکن" in r["message"]
    assert live.next_auto_fetch_at() == datetime(2026, 11, 7, 9, 10, tzinfo=TEHRAN)


def test_pricetoday_source_from_morning_to_noon(tmp_path, monkeypatch):
    calls = []

    def fake_get(url, timeout):
        calls.append(url)
        return 200, (FIXTURES / "pricetoday_usd_sample.json").read_text(encoding="utf-8")
    monkeypatch.setattr(fx, "_http_get", fake_get)
    cfg = config.load_config(write_settings(tmp_path, provider="pricetoday"))
    s = Service(replace(cfg, source=replace(cfg.source, mode="live")))
    at(2026, 10, 31, 13, 30)
    s.init()
    at(2026, 11, 1, 9, 0)
    r = s.scheduled_fetch()
    assert r["status"] == "held" and r["value_toman"] == 270500 and "۴٫۰۴ درصد بالاتر" in r["message"]
    assert calls == ["https://api.priceto.day/v1/latest/irr/usd"]
    at(2026, 11, 1, 9, 5)
    run_id = s.decide_held(r["rate_id"], "finance", accept=True)["run_id"]
    st = s.state()
    assert st["run"]["id"] == run_id and st["last_accepted"]["source"] == "pricetoday"
    assert (st["last_accepted"]["slug"], st["last_accepted"]["field"]) == ("usd", "price")
    assert st["last_accepted"]["source_updated_at"] == "2026-10-31T16:29:55+00:00"
    assert s.next_auto_fetch_at() == datetime(2026, 11, 1, 10, 0, tzinfo=TEHRAN)
    at(2026, 11, 1, 10, 0)
    assert s.scheduled_fetch()["status"] == "ok"
    at(2026, 11, 1, 12, 30)
    r = s.scheduled_fetch()
    assert r["status"] == "stale" and "از ساعت کاری" in r["message"]
    assert len(calls) == 3


def test_product_import_preview_and_apply(svc, tmp_path):
    s = svc()
    at(2026, 10, 31, 10, 0)
    s.manual_rate(260000, "finance", "x")
    assert s.state()["run"]["status"] == "no_change"
    src = s.cfg.paths.products_xlsx
    dst = tmp_path / "edited.xlsx"
    shutil.copyfile(src, dst)
    wb = load_workbook(dst)
    ws = wb["محصولات"]
    assert ws["B6"].value == "TMP-01"
    ws["O6"] = 23000000
    ws.delete_rows(24)
    wb.save(dst)
    pv = s.preview_import(dst)
    assert not pv["errors"] and pv["count"] == 26
    assert pv["diff"]["removed"] == ["TMP-06"]
    assert any(x["key"] == "TMP-01" and x["field"] == "base_price_rial" for x in pv["diff"]["changed"])
    s.import_products(dst, "admin")
    run = s.state()["run"]
    assert run["needs_publish"] and run["removed"] == ["TMP-06"] and len(run["rows"]) == 26
    flagged = [x["product_key"] for x in run["rows"] if x["flag_publish"]]
    assert flagged == ["TMP-01"]
    assert len([p for p in s.products() if p["active"]]) == 26


def test_import_rejects_bad_rows(svc, tmp_path):
    s = svc()
    dst = tmp_path / "bad.xlsx"
    shutil.copyfile(s.cfg.paths.products_xlsx, dst)
    wb = load_workbook(dst)
    ws = wb["محصولات"]
    ws["O7"] = "بیست میلیون"
    ws["B8"] = "TMP-01"
    wb.save(dst)
    pv = s.preview_import(dst)
    assert len(pv["errors"]) == 2
    with pytest.raises(ServiceError):
        s.import_products(dst, "admin")
    assert s.products()[0]["base_price_rial"] == 22000000


def test_users_and_last_admin(svc):
    s = svc()
    admin = s.create_user("admin", "a-strong-pass", "admin", "مدیر", "setup")
    s.create_user("sales1", "sales-pass-1", "viewer", "فروش ۱", "admin")
    with pytest.raises(ServiceError):
        s.create_user("sales1", "another-pass", "viewer", "", "admin")
    with pytest.raises(ServiceError):
        s.create_user("x", "short", "viewer", "", "admin")
    assert s.verify_login("ADMIN", "a-strong-pass")["role"] == "admin"
    assert s.verify_login("admin", "wrong") is None
    with pytest.raises(ServiceError):
        s.update_user(admin, "admin", role="viewer")
    with pytest.raises(ServiceError):
        s.update_user(admin, "admin", active=False)
    uid = s.user_id_by_name("sales1")
    s.update_user(uid, "admin", active=False)
    assert s.verify_login("sales1", "sales-pass-1") is None
    actions = s.audit_actions()
    assert {"login", "login_failed", "user_create", "user_update"} <= set(actions)


def test_backup(svc):
    s = svc()
    path = s.backup()
    assert path.exists() and path.stat().st_size > 10000
