import io
import re
from datetime import datetime

import pytest
from openpyxl import load_workbook

from borna_pricing import clock, config
from borna_pricing.clock import TEHRAN
from borna_pricing.market.service import MarketService
from borna_pricing.service import Service
from borna_pricing.web import create_app

from conftest import FIXTURES, FakeWeb, fake_fetcher_factory, write_settings

LIST_URL = "https://shop.example.ir/cat/o2"
ITEMLIST = (FIXTURES / "market" / "synthetic_itemlist.html").read_text(encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    cfg = config.load_config(write_settings(tmp_path))
    svc = Service(cfg)
    clock.set_fixed_now(datetime(2026, 10, 31, 13, 30, tzinfo=TEHRAN))
    svc.init()
    svc.create_user("admin", "admin-pass-1", "admin", "مدیر سامانه", "test")
    svc.create_user("finance", "finance-pass", "approver", "مدیر مالی", "test")
    svc.create_user("sales", "sales-pass-1", "viewer", "کارشناس فروش", "test")
    web = FakeWeb({LIST_URL: (200, "text/html; charset=utf-8", ITEMLIST)})
    ms = MarketService(svc, fake_fetcher_factory(web))
    app = create_app(cfg, svc, ms)
    app.config["TESTING"] = True
    return app, svc, ms


def login(client, user, pw):
    client.get("/login")
    with client.session_transaction() as s:
        tok = s.get("_csrf")
    r = client.post("/login", data={"username": user, "password": pw, "_csrf": tok})
    assert r.status_code == 302
    with client.session_transaction() as s:
        return s["_csrf"]


def text(r):
    return r.get_data(as_text=True)


def add_source(c, tok):
    r = c.post("/market/sources/new", data={
        "_csrf": tok, "key": "shopx", "name": "فروشگاه نمونه", "kind": "shop", "domain": "shop.example.ir",
        "start_urls": LIST_URL, "unit": "toman", "price_level": "retail", "parser": "auto", "interval_hours": "24",
        "delay_seconds": "3", "max_pages": "5", "active": "1", "action": "save"})
    assert r.status_code == 302, text(r)[:500]
    sid = int(re.search(r"/market/sources/(\d+)", r.headers["Location"]).group(1))
    r = c.post(f"/market/sources/{sid}/policy", data={"_csrf": tok, "status": "approved",
                                                      "note": "robots.txt و شرایط استفاده بررسی شد"})
    assert r.status_code == 302
    return sid


def test_roles_and_csrf(env):
    app, _, _ = env
    sales = app.test_client()
    login(sales, "sales", "sales-pass-1")
    for url in ("/market", "/market/listings", "/market/manual", "/market/reports", "/market/products/FIB101-480"):
        assert sales.get(url).status_code == 200, url
    for url in ("/market/review", "/market/observations", "/market/sources", "/market/agents"):
        assert sales.get(url).status_code == 403, url
    fin = app.test_client()
    tok = login(fin, "finance", "finance-pass")
    assert fin.get("/market/sources").status_code == 200
    assert fin.get("/market/sources/new").status_code == 403
    assert fin.post("/market/agents/pause", data={"_csrf": tok, "paused": "1"}).status_code == 403
    assert fin.post("/market/review/approve-strong", data={}).status_code == 400
    page = text(sales.get("/market"))
    assert "بازار و رقبا" in page and "هنوز داده‌ای از بازار نیست" in page
    assert 'href="/market"' in text(sales.get("/"))


def test_full_flow_through_web(env):
    app, svc, ms = env
    admin = app.test_client()
    tok = login(admin, "admin", "admin-pass-1")
    sid = add_source(admin, tok)
    r = admin.post(f"/market/sources/{sid}/run", data={"_csrf": tok}, follow_redirects=True)
    assert "۱ صفحه خوانده شد" in text(r) and "۴ کالا" in text(r)
    review = text(admin.get("/market/review"))
    assert "FIB101-480" in review and "تطبیق قوی" in review and "پارت‌نامبر یکسان" in review
    r = admin.post("/market/review/approve-strong", data={"_csrf": tok}, follow_redirects=True)
    assert "۲ تطبیق قوی تأیید شد" in text(r)
    me17 = next(e for e in ms.review_queue() if "ME17" in e["title"])
    mid = next(m["id"] for m in me17["proposals"] if m["product_key"] == "FIB201-400")
    assert admin.post(f"/market/matches/{mid}/decide", data={"_csrf": tok, "decision": "accept"}).status_code == 302
    ov = text(admin.get("/market"))
    assert "۳٬۴۰۰٬۰۰۰" in ov and "ارزان‌تر" in ov
    prod = text(admin.get("/market/products/FIB101-480"))
    assert "۳٬۴۰۰٬۰۰۰" in prod and "مدرک" in prod and "<svg" in prod
    listing = next(e for e in ms.listings() if "OZA632-SZ2" in e["title"])
    page = text(admin.get(f"/market/listings/{listing['id']}"))
    assert "تأییدشده" in page and "OZA632-SZ2" in page
    assert admin.get("/market/listings?state=unmatched").status_code == 200
    test = text(admin.post(f"/market/sources/{sid}", data={"_csrf": tok, "action": "test", "parser": "auto"}))
    assert "نتیجه آزمون: ۴ کالا" in test
    assert admin.get("/market/agents").status_code == 200
    r = admin.post("/market/agents/matcher/run", data={"_csrf": tok}, follow_redirects=True)
    assert "اجرا تمام شد" in text(r)
    r = admin.post("/market/reports/build", data={"_csrf": tok})
    assert r.status_code == 302
    rid = int(r.headers["Location"].rsplit("/", 1)[1])
    rep = text(admin.get(f"/market/reports/{rid}"))
    assert "نکته‌های این هفته" in rep and "FIB101-480" in rep
    x = admin.get(f"/market/reports/{rid}.xlsx")
    assert x.status_code == 200 and load_workbook(io.BytesIO(x.data)).sheetnames == ["خلاصه", "کالاها"]
    r = admin.post("/market/agents/pause", data={"_csrf": tok, "paused": "1"}, follow_redirects=True)
    assert "متوقف شد" in text(r) and ms.paused()
    audit = text(admin.get("/admin/audit"))
    assert "مجوز پایش منبع" in audit and "تأیید گروهی تطبیق" in audit


def test_evidence_is_never_rendered_as_html(env):
    app, svc, ms = env
    admin = app.test_client()
    tok = login(admin, "admin", "admin-pass-1")
    sid = add_source(admin, tok)
    admin.post(f"/market/sources/{sid}/run", data={"_csrf": tok})
    snap = ms.snapshots(sid)[0]
    r = admin.get(f"/market/evidence/{snap['id']}")
    assert r.status_code == 200 and r.mimetype == "text/plain"
    assert "attachment" in r.headers["Content-Disposition"] and r.headers["X-Content-Type-Options"] == "nosniff"
    assert admin.get("/market/evidence/99999").status_code == 404


def test_sales_manual_entry_then_approval(env):
    app, svc, ms = env
    sales = app.test_client()
    tok = login(sales, "sales", "sales-pass-1")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    r = sales.post("/market/manual", data={
        "_csrf": tok, "seller": "بنکدار الف", "product_key": "FIB201-400", "price": "۲٬۴۰۰٬۰۰۰", "unit": "toman",
        "price_level": "wholesale", "min_qty": "100", "tier": "aftermarket", "availability": "in_stock",
        "channel": "whatsapp", "note": "", "evidence": (io.BytesIO(png), "price.png")},
        content_type="multipart/form-data", follow_redirects=True)
    assert "بعد از تأیید" in text(r)
    r = sales.post("/market/manual", data={"_csrf": tok, "seller": "x", "price": "abc", "note": "تلفنی گفت"},
                   follow_redirects=True)
    assert "قیمت را با رقم" in text(r) or "عنوان" in text(r)
    fin = app.test_client()
    ftok = login(fin, "finance", "finance-pass")
    page = text(fin.get("/market/observations"))
    assert "بنکدار الف" in page and "در انتظار تأیید" in page and "FIB201-400" in page
    assert "بنکدار الف" not in text(fin.get("/market/review"))
    manual_src = next(s for s in ms.sources() if s["kind"] == "manual")
    assert fin.get(f"/market/sources/{manual_src['id']}").status_code == 404
    oid = ms.observations_to_review()[0]["id"]
    snap_id = ms.observations_to_review()[0]["snapshot_id"]
    img = sales.get(f"/market/evidence/{snap_id}")
    assert img.status_code == 200 and img.mimetype == "image/png"
    r = fin.post(f"/market/observations/{oid}/decide", data={"_csrf": ftok, "decision": "accept"}, follow_redirects=True)
    assert "قیمت تأیید شد" in text(r)
    row = {r["product"]["key"]: r for r in ms.overview()["table"]}["FIB201-400"]
    assert row["stats"]["wholesale"]["median"] == 24_000_000


def test_specs_admin_only(env):
    app, svc, ms = env
    fin = app.test_client()
    tok = login(fin, "finance", "finance-pass")
    assert fin.post("/market/products/FIB101-480/specs", data={"_csrf": tok, "attr": "xref", "value": "0258000001"}).status_code == 403
    admin = app.test_client()
    tok = login(admin, "admin", "admin-pass-1")
    r = admin.post("/market/products/FIB101-480/specs", data={"_csrf": tok, "attr": "xref", "value": "0258000001"},
                   follow_redirects=True)
    assert "ویژگی ثبت شد" in text(r) and "0258000001" in text(r)
    r = admin.post("/market/products/FIB101-480/specs", data={"_csrf": tok, "attr": "ecu", "value": "؟"},
                   follow_redirects=True)
    assert "ECU باید" in text(r)
