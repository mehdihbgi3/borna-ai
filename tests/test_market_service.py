import json
from datetime import datetime, timedelta
from io import BytesIO

import pytest
from openpyxl import load_workbook

from borna_pricing import clock, config
from borna_pricing.clock import TEHRAN
from borna_pricing.market import report as market_report
from borna_pricing.market.service import MarketService
from borna_pricing.service import Service, ServiceError

from conftest import FIXTURES, FakeWeb, fake_fetcher_factory, write_settings

LIST_URL = "https://shop.example.ir/cat/o2"
ITEMLIST = (FIXTURES / "market" / "synthetic_itemlist.html").read_text(encoding="utf-8")
SOURCE = {"key": "shopx", "name": "فروشگاه نمونه", "kind": "shop", "domain": "shop.example.ir",
          "start_urls": LIST_URL, "unit": "toman", "price_level": "retail", "parser": "auto", "interval_hours": "24",
          "delay_seconds": "3", "max_pages": "5", "active": "1"}


def at(y, m, d, hh, mm):
    clock.set_fixed_now(datetime(y, m, d, hh, mm, tzinfo=TEHRAN))


@pytest.fixture
def env(tmp_path):
    cfg = config.load_config(write_settings(tmp_path))
    svc = Service(cfg)
    at(2026, 10, 31, 13, 30)
    svc.init()
    web = FakeWeb({LIST_URL: (200, "text/html; charset=utf-8", ITEMLIST)})
    ms = MarketService(svc, fake_fetcher_factory(web))
    return svc, ms, web


def approved_source(ms, **over):
    sid = ms.save_source({**SOURCE, **over}, "admin")
    ms.set_policy(sid, "approved", "robots.txt و شرایط استفاده بررسی شد", "admin")
    return sid


def obs_of(ms, title_part):
    with ms.db() as c:
        return [dict(r) for r in c.execute(
            "SELECT o.* FROM observations o JOIN external_products e ON e.id=o.external_product_id WHERE e.title LIKE ?"
            " ORDER BY o.id", (f"%{title_part}%",))]


def test_source_validation_and_policy(env):
    _, ms, _ = env
    with pytest.raises(ServiceError):
        ms.save_source({**SOURCE, "key": "Bad Key"}, "admin")
    with pytest.raises(ServiceError):
        ms.save_source({**SOURCE, "domain": "localhost"}, "admin")
    with pytest.raises(ServiceError):
        ms.save_source({**SOURCE, "start_urls": "https://evil.example.com/x"}, "admin")
    with pytest.raises(ServiceError):
        ms.save_source({**SOURCE, "parser": "regex", "item_regex": "(?P<title>x)"}, "admin")
    sid = ms.save_source(SOURCE, "admin")
    with pytest.raises(ServiceError):
        ms.save_source(SOURCE, "admin")
    with pytest.raises(ServiceError, match="مجاز نشده"):
        ms.collect_source(sid)
    with pytest.raises(ServiceError):
        ms.set_policy(sid, "approved", "", "admin")
    ms.set_policy(sid, "approved", "robots.txt بررسی شد", "admin")
    assert ms.get_source(sid)["policy_status"] == "approved"
    ms.save_source({**SOURCE, "domain": "other.example.ir", "start_urls": "https://other.example.ir/x"}, "admin", sid)
    assert ms.get_source(sid)["policy_status"] == "pending"
    assert ms.get_source(sid)["delay_seconds"] >= 3


def test_collect_parse_store_and_match(env):
    svc, ms, web = env
    sid = approved_source(ms)
    res = ms.run_agent("price_watcher", "manual", "finance")
    assert res["status"] == "ok", res
    st = res["stats"]
    assert st["sources"] == 1 and st["pages"] == 1 and st["new_listings"] == 4 and st["observations"] == 4
    assert web.calls[0].endswith("/robots.txt")
    a = obs_of(ms, "OZA632-SZ2")[0]
    assert (a["kind"], a["price_rial"], a["unit"], a["price_level"], a["status"]) == ("price", 34_000_000, "toman", "retail", "ok")
    b = obs_of(ms, "258006027")[0]
    assert b["price_rial"] == 29_500_000 and b["list_price_rial"] == 33_000_000
    c = obs_of(ms, "ME17")[0]
    assert c["kind"] == "quote_only" and c["price_rial"] is None and c["availability"] == "out_of_stock"
    snaps = ms.snapshots(sid)
    assert len(snaps) == 1 and snaps[0]["n_items"] == 4
    assert ms.snapshot_bytes(snaps[0]).decode("utf-8") == ITEMLIST
    by_title = {r["title"]: r for r in ms.listings()}
    assert by_title["بکس سنسور اکسیژن"]["match_state"] == "unmatched"
    q = {e["title"]: e for e in ms.review_queue()}
    strong = q["سنسور اکسیژن زیمنس سوکت طوسی NTK اصل ژاپن OZA632-SZ2 سیم 50 سانت"]["proposals"]
    assert [m["product_key"] for m in strong] == ["FIB101-480"] and strong[0]["verdict"] == "strong"
    me17 = q["سنسور اکسیژن ME17 بوش سوکت مشکی"]["proposals"]
    assert me17[0]["product_key"] == "FIB201-400"
    assert ms.approve_strong("finance") == 2
    ov = {r["product"]["key"]: r for r in ms.overview()["table"]}
    row = ov["FIB101-480"]
    assert row["official"] == 21_000_000 and row["stats"]["retail"]["median"] == 34_000_000
    assert row["ref_level"] == "retail" and float(row["position"]) == pytest.approx(21 / 34 - 1)


def test_recollect_change_detection_and_quarantine(env):
    svc, ms, web = env
    approved_source(ms)
    ms.run_agent("price_watcher", "manual", "finance")
    res = ms.run_agent("price_watcher", "manual", "finance")
    assert res["stats"]["observations"] == 0
    web.pages[LIST_URL] = (200, "text/html", ITEMLIST.replace('"price":"3400000"', '"price":"3600000"'))
    ms.run_agent("price_watcher", "manual", "finance")
    rows = obs_of(ms, "OZA632-SZ2")
    assert [r["price_rial"] for r in rows] == [34_000_000, 36_000_000] and rows[-1]["status"] == "ok"
    web.pages[LIST_URL] = (200, "text/html", ITEMLIST.replace('"price":"3400000"', '"price":"9900000"'))
    ms.run_agent("price_watcher", "manual", "finance")
    last = obs_of(ms, "OZA632-SZ2")[-1]
    assert last["status"] == "quarantined" and "تغییر" in last["status_note"]
    web.pages[LIST_URL] = (200, "text/html", ITEMLIST.replace('"price":"3400000"', '"price":"34000000000"'))
    ms.run_agent("price_watcher", "manual", "finance")
    last = obs_of(ms, "OZA632-SZ2")[-1]
    assert last["status"] == "quarantined" and "بازه" in last["status_note"]
    pending = [p for p in ms.observations_to_review() if "OZA632-SZ2" in p["title"]]
    assert len(pending) == 2
    ms.decide_observation(pending[0]["id"], True, "finance", "قیمت درست است")
    ms.decide_observation(pending[1]["id"], False, "finance", "خطای واحد")
    assert [p["title"] for p in ms.observations_to_review()] == ["بکس سنسور اکسیژن"]
    with pytest.raises(ServiceError):
        ms.decide_observation(pending[0]["id"], True, "finance")


def test_failed_fetch_marks_source(env):
    svc, ms, web = env
    sid = approved_source(ms, start_urls="https://shop.example.ir/missing")
    res = ms.run_agent("price_watcher", "manual", "finance")
    assert res["status"] == "failed"
    src = ms.get_source(sid)
    assert src["failure_count"] == 1 and "404" in src["last_error"]


def test_manual_observation_roles_and_evidence(env):
    svc, ms, _ = env
    with pytest.raises(ServiceError, match="مدرک"):
        ms.manual_observation({"seller": "بنکدار الف", "price": "2500000", "product_key": "FIB101-480"}, "sales", "viewer")
    oid = ms.manual_observation({"seller": "بنکدار الف", "price": "۲٬۵۰۰٬۰۰۰", "unit": "toman", "price_level": "wholesale",
                                 "min_qty": "100", "channel": "phone", "product_key": "FIB101-480", "tier": "aftermarket",
                                 "note": "تماس تلفنی با فروش بنکدار الف"}, "sales", "viewer")
    pend = ms.observations_to_review()
    assert [p["id"] for p in pend] == [oid] and pend[0]["status"] == "pending" and pend[0]["min_qty"] == 100
    assert ms.overview()["kpi"]["matched"] == 0
    ms.decide_observation(oid, True, "finance")
    row = {r["product"]["key"]: r for r in ms.overview()["table"]}["FIB101-480"]
    assert row["stats"]["wholesale"]["median"] == 25_000_000 and row["n_listings"] == 1
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    oid2 = ms.manual_observation({"seller": "بنکدار ب", "price": "2400000", "unit": "toman", "price_level": "wholesale",
                                  "product_key": "FIB101-480", "channel": "whatsapp"}, "finance", "approver",
                                 upload=(png, "list.png"))
    with ms.db() as c:
        o = c.execute("SELECT o.status, sn.kind, sn.content_type FROM observations o JOIN snapshots sn"
                      " ON sn.id=o.snapshot_id WHERE o.id=?", (oid2,)).fetchone()
    assert tuple(o) == ("ok", "upload", "image/png")
    with pytest.raises(ServiceError, match="فایل"):
        ms.manual_observation({"seller": "x", "price": "2400000", "product_key": "FIB101-480"}, "finance", "approver",
                              upload=(b"<svg/>", "a.svg"))
    oid3 = ms.manual_observation({"seller": "بنکدار ج", "price": "24000000", "unit": "toman", "product_key": "FIB101-480",
                                  "note": "پیام واتس‌اپ"}, "finance", "approver")
    with ms.db() as c:
        assert c.execute("SELECT status FROM observations WHERE id=?", (oid3,)).fetchone()[0] == "quarantined"


def test_match_decisions_link_unlink_and_specs(env):
    svc, ms, _ = env
    approved_source(ms)
    ms.run_agent("price_watcher", "manual", "finance")
    q = {e["title"]: e for e in ms.review_queue()}
    me17 = q["سنسور اکسیژن ME17 بوش سوکت مشکی"]
    sub = next(m for m in me17["proposals"] if m["product_key"] == "FIB109-400")
    ms.decide_match(sub["id"], False, "finance", "طرح دیگر")
    main = next(m for m in me17["proposals"] if m["product_key"] == "FIB201-400")
    ms.decide_match(main["id"], True, "finance")
    lst = ms.listing(me17["id"])
    assert lst["match_state"] == "matched"
    assert [m["product_key"] for m in lst["matches"] if m["status"] == "approved"] == ["FIB201-400"]
    with pytest.raises(ServiceError, match="قبلاً"):
        ms.manual_link(me17["id"], "FIB202-400", "finance")
    approved = next(m for m in lst["matches"] if m["status"] == "approved")
    ms.unlink(approved["id"], "finance")
    assert ms.listing(me17["id"])["match_state"] == "unmatched"
    ms.manual_link(me17["id"], "FIB109-400", "finance", "انتخاب کارشناس")
    m = next(m for m in ms.listing(me17["id"])["matches"] if m["status"] == "approved")
    assert m["product_key"] == "FIB109-400" and m["relation"] == "substitute" and m["verdict"] == "manual"
    tool = next(e for e in ms.listings() if e["title"] == "بکس سنسور اکسیژن")
    assert tool["match_state"] == "unmatched"
    ms.add_spec("FIB101-480", "xref", "0 258 999 001", "admin")
    assert ms.specs("FIB101-480")[0]["value"] == "0258999001"
    prof = next(p for p in ms.profiles() if p["key"] == "FIB101-480")
    assert "0258999001" in prof["part_nos"]
    ms.add_spec("FIB101-480", "wire_count", "۴", "admin")
    ms.add_spec("FIB101-480", "wire_count", "4", "admin")
    assert [s["value"] for s in ms.specs("FIB101-480") if s["attr"] == "wire_count"] == ["4"]
    with pytest.raises(ServiceError):
        ms.add_spec("FIB101-480", "ecu", "نامعلوم", "admin")
    ms.remove_spec("FIB101-480", "xref", "0258999001", "admin")
    with pytest.raises(ServiceError):
        ms.remove_spec("FIB101-480", "xref", "0258999001", "admin")


def test_tier_and_own_overrides(env):
    svc, ms, _ = env
    approved_source(ms)
    ms.run_agent("price_watcher", "manual", "finance")
    e = next(x for x in ms.listings() if "OZA632-SZ2" in x["title"])
    assert e["tier"] == "genuine"
    ms.set_tier(e["id"], "aftermarket", "finance")
    ms.run_agent("price_watcher", "manual", "finance")
    assert ms.listing(e["id"])["tier"] == "aftermarket"
    ms.set_own(e["id"], True, "finance")
    ms.approve_strong("finance")
    row = {r["product"]["key"]: r for r in ms.overview()["table"]}["FIB101-480"]
    assert row["n_listings"] == 0 and row["own_price"] == 34_000_000
    with pytest.raises(ServiceError):
        ms.set_tier(e["id"], "fake", "finance")


def test_own_seller_detection(env):
    _, ms, _ = env
    assert ms.own_seller("برنا سنسور اکسیژن فارس") and not ms.own_seller("فروشگاه دیگر") and not ms.own_seller(None)


def test_agents_schedule_pause_and_report(env):
    svc, ms, _ = env
    approved_source(ms)
    at(2026, 10, 31, 9, 0)
    done = {d["agent"]: d for d in ms.scheduled_tick()}
    assert done["price_watcher"]["status"] == "ok" and "market_analyst" not in done
    assert ms.scheduled_tick() == [] or all(d["agent"] != "price_watcher" for d in ms.scheduled_tick())
    ms.approve_strong("finance")
    me17 = {e["title"]: e for e in ms.review_queue()}["سنسور اکسیژن ME17 بوش سوکت مشکی"]
    ms.decide_match(next(m["id"] for m in me17["proposals"] if m["product_key"] == "FIB201-400"), True, "finance")
    at(2026, 10, 31, 10, 5)
    done = {d["agent"]: d for d in ms.scheduled_tick()}
    assert done["market_analyst"]["status"] == "ok"
    rid = done["market_analyst"]["stats"]["report_id"]
    rep = ms.report(rid)
    rows = {r["key"]: r for r in rep["data"]["rows"]}
    assert rows["FIB101-480"]["median"] == 34_000_000 and rows["FIB101-480"]["level"] == "retail"
    assert any(i["kind"] == "position" and i["key"] == "FIB101-480" for i in rep["data"]["insights"])
    assert any(i["kind"] == "oos" for i in rep["data"]["insights"])
    assert all(d["agent"] != "market_analyst" for d in ms.scheduled_tick())
    wb = load_workbook(BytesIO(market_report.xlsx(rep, "برنا سنسور")))
    assert wb.sheetnames == ["خلاصه", "کالاها"] and wb["کالاها"].max_row == 28
    at(2026, 11, 2, 9, 0)
    ms.set_paused(True, "admin")
    assert ms.scheduled_tick() == []
    res = ms.run_agent("matcher", "manual", "finance")
    assert res["status"] == "skipped"
    ms.set_paused(False, "admin")
    with pytest.raises(ServiceError):
        ms.run_agent("nope")
    overview = {a["key"]: a for a in ms.agents_overview()}
    assert set(overview) == {"price_watcher", "matcher", "market_analyst"}
    assert overview["price_watcher"]["last"]["status"] == "ok"


def test_report_shows_competitors_lagging_the_dollar(env):
    svc, ms, web = env
    approved_source(ms)
    at(2026, 10, 24, 9, 0)
    svc.manual_rate(260000, "finance", "مبنا")
    ms.run_agent("price_watcher", "manual", "finance")
    ms.approve_strong("finance")
    at(2026, 10, 31, 9, 0)
    svc.manual_rate(280000, "finance", "صرافی")
    rid = ms.build_report("finance")
    data = ms.report(rid)["data"]
    assert data["fx_start"] == 260000 and data["fx_end"] == 280000
    assert any(i["kind"] == "lag" and i["key"] == "FIB101-480" for i in data["insights"])


def test_interrupted_runs_are_recovered(env):
    _, ms, _ = env
    with ms.db() as c:
        c.execute("INSERT INTO agent_runs(agent, trigger, started_at, status, created_by) VALUES"
                  " ('matcher','schedule','2026-10-31T06:00:00+00:00','running','system')")
    assert ms.recover_interrupted() == 1
    assert ms.agent_runs(1)[0]["status"] == "failed"


def test_parser_test_uses_last_snapshot(env):
    _, ms, _ = env
    sid = approved_source(ms)
    with pytest.raises(ServiceError):
        ms.test_parser(sid)
    ms.run_agent("price_watcher", "manual", "finance")
    out = ms.test_parser(sid)
    assert out["count"] == 4 and out["items"][0]["rial"] == 34_000_000
    with pytest.raises(ServiceError):
        ms.test_parser(sid, {"parser": "regex", "item_regex": "(?P<title>"})
