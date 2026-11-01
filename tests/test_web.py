import io
import re
import shutil
from datetime import datetime

import pytest
from openpyxl import load_workbook

from borna_pricing import clock, config
from borna_pricing.clock import TEHRAN
from borna_pricing.service import Service
from borna_pricing.web import create_app

from conftest import write_settings


def at(y, m, d, hh, mm):
    clock.set_fixed_now(datetime(y, m, d, hh, mm, tzinfo=TEHRAN))


@pytest.fixture
def env(tmp_path):
    cfg = config.load_config(write_settings(tmp_path))
    svc = Service(cfg)
    at(2026, 10, 31, 13, 30)
    svc.init()
    svc.create_user("admin", "admin-pass-1", "admin", "مدیر سامانه", "test")
    svc.create_user("finance", "finance-pass", "approver", "مدیر مالی", "test")
    svc.create_user("sales", "sales-pass-1", "viewer", "کارشناس فروش", "test")
    app = create_app(cfg, svc)
    app.config["TESTING"] = True
    return app, svc, tmp_path


def csrf(client):
    client.get("/login")
    with client.session_transaction() as s:
        return s.get("_csrf")


def login(client, user, pw):
    tok = csrf(client)
    r = client.post("/login", data={"username": user, "password": pw, "_csrf": tok})
    assert r.status_code == 302, r.data.decode()[:300]
    with client.session_transaction() as s:
        return s["_csrf"]


def text(resp):
    return resp.get_data(as_text=True)


def test_login_required_and_wrong_password(env):
    app, _, _ = env
    c = app.test_client()
    r = c.get("/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    tok = csrf(c)
    r = c.post("/login", data={"username": "sales", "password": "nope", "_csrf": tok})
    assert r.status_code == 401 and "درست نیست" in text(r)


def test_post_without_csrf_is_rejected(env):
    app, _, _ = env
    c = app.test_client()
    login(c, "finance", "finance-pass")
    assert c.post("/fetch").status_code == 400


def test_login_throttle(env):
    app, _, _ = env
    c = app.test_client()
    tok = csrf(c)
    for _ in range(5):
        c.post("/login", data={"username": "sales", "password": "bad", "_csrf": tok})
    r = c.post("/login", data={"username": "sales", "password": "sales-pass-1", "_csrf": tok})
    assert r.status_code == 401 and "دقیقه دیگر" in text(r)


def test_viewer_sees_official_list_only(env):
    app, _, _ = env
    c = app.test_client()
    tok = login(c, "sales", "sales-pass-1")
    page = text(c.get("/"))
    assert "لیست قیمت رسمی، شماره ۱" in page and "منقضی" in page
    assert "۲۲٬۰۰۰٬۰۰۰" in page and "۳۴٬۵۰۰٬۰۰۰" in page
    assert "انتشار لیست تازه" not in page and "ورود نرخ دستی" not in page
    assert c.post("/fetch", data={"_csrf": tok}).status_code == 403
    assert c.get("/admin/users").status_code == 403
    r = c.get("/")
    for h in ("Content-Security-Policy", "X-Frame-Options", "X-Content-Type-Options"):
        assert h in r.headers


def test_approver_flow_fetch_hold_accept_publish(env):
    app, svc, _ = env
    c = app.test_client()
    tok = login(c, "finance", "finance-pass")
    at(2026, 10, 31, 13, 33)
    r = c.post("/fetch", data={"_csrf": tok}, follow_redirects=True)
    page = text(r)
    assert "تأیید لازم دارد" in page and "نرخ تازه منتظر تصمیم شماست: ۲۷۳٬۴۰۰ تومان" in page
    held_id = svc.state()["held"]["id"]
    page = text(c.post(f"/rates/{held_id}/decide", data={"_csrf": tok, "decision": "accept"}, follow_redirects=True))
    assert "لیست تازه آماده انتشار است" in page and "confirm-approval" in page
    assert "۲۳٬۰۰۰٬۰۰۰" in page and "۳۶٬۵۰۰٬۰۰۰" in page and "+۵٫۱۵٪" in page
    run_id = svc.state()["run"]["id"]
    page = text(c.post(f"/runs/{run_id}/publish", data={"_csrf": tok}, follow_redirects=True))
    assert "تیک بزنید" in page
    page = text(c.post(f"/runs/{run_id}/publish", data={"_csrf": tok, "confirm": "1", "note": "آزمون"},
                       follow_redirects=True))
    assert "لیست قیمت شماره ۲ منتشر شد" in page
    assert "معتبر تا ساعت ۱۷:۰۰ امروز" in page and "انتشار با تأیید" in page
    page = text(c.post("/fetch", data={"_csrf": tok}, follow_redirects=True))
    assert "دریافت بعدی از ساعت ۱۴:۳۳" in page


def test_manual_rate_validation_in_form(env):
    app, svc, _ = env
    c = app.test_client()
    tok = login(c, "finance", "finance-pass")
    page = text(c.post("/rates/manual", data={"_csrf": tok, "toman": "۲٬۷۳۴٬۰۰۰", "note": "x"}, follow_redirects=True))
    assert "به تومان وارد کنید" in page
    page = text(c.post("/rates/manual", data={"_csrf": tok, "toman": "۲۷۳٬۴۰۰", "note": "صرافی"}, follow_redirects=True))
    assert "نرخ دستی ثبت شد" in page and svc.state()["last_accepted"]["value_toman"] == 273400


def test_pricelist_html_and_excel(env):
    app, svc, _ = env
    at(2026, 10, 31, 13, 40)
    svc.manual_rate(273400, "finance", "x")
    svc.publish(svc.state()["run"]["id"], "finance", confirm_approval=True)
    c = app.test_client()
    login(c, "sales", "sales-pass-1")
    page = text(c.get("/pricelist"))
    assert "لیست قیمت" in page and "۳۶٬۵۰۰٬۰۰۰" in page and "۲٬۳۰۰٬۰۰۰" in page
    assert "۲۷۳٬۴۰۰" in page and "شنبه ۹ آبان ۱۴۰۵" in page
    old = text(c.get("/pricelist/1"))
    assert "اعتبار ندارد" in old and "لیست تازه‌تری" in old
    r = c.get("/pricelist.xlsx")
    assert r.status_code == 200 and "Borna_PriceList_14050809_1340_no2.xlsx" in r.headers["Content-Disposition"]
    wb = load_workbook(io.BytesIO(r.data))
    ws = wb.active
    assert ws.sheet_view.rightToLeft
    values = [ws.cell(row=i, column=5).value for i in range(1, ws.max_row + 1)]
    assert 36500000 in values and 23000000 in values
    assert c.get("/pricelist/99").status_code == 404


def test_history_pages_render_charts(env):
    app, svc, _ = env
    at(2026, 10, 30, 10, 0)
    svc.manual_rate(262000, "finance", "x")
    at(2026, 10, 31, 10, 0)
    svc.manual_rate(268000, "finance", "y")
    svc.publish(svc.state()["run"]["id"], "finance")
    c = app.test_client()
    login(c, "sales", "sales-pass-1")
    page = text(c.get("/rates"))
    assert page.count("<svg") >= 1 and "نرخ مبنا ۲۶۰٬۰۰۰" in page and "application/json" in page
    assert "ورود دستی" in page
    page = text(c.get("/products/FIB206-350"))
    assert "<svg" in page and "سنسور اکسیژن بوش پنج سیم EF7" in page and "۰۲۵۸۰۰۷۳۴۲" not in page
    assert "0258007342" in page
    assert c.get("/products/NOPE").status_code == 404
    assert "۲" in text(c.get("/publications"))


def test_admin_pages_users_and_import(env):
    app, svc, tmp = env
    c = app.test_client()
    tok = login(c, "admin", "admin-pass-1")
    page = text(c.post("/admin/users", data={"_csrf": tok, "action": "create", "username": "ceo", "password": "ceo-pass-12",
                                             "role": "approver", "full_name": "مدیرعامل"}, follow_redirects=True))
    assert "ساخته شد" in page and "مدیرعامل" in page
    page = text(c.get("/admin/settings"))
    assert "تنظیم نشده" in page and "PUT_YOUR_TOKEN_HERE" not in page and "الان‌چند" in page
    assert "۵۰۰٬۰۰۰" in page
    src = svc.cfg.paths.products_xlsx
    dst = tmp / "edit.xlsx"
    shutil.copyfile(src, dst)
    wb = load_workbook(dst)
    assert wb["محصولات"]["B29"].value == "FIB206-350"
    wb["محصولات"]["O29"] = 35000000
    wb.save(dst)
    with open(dst, "rb") as fh:
        page = text(c.post("/admin/products/preview", data={"_csrf": tok, "file": (fh, "edit.xlsx")},
                           content_type="multipart/form-data"))
    assert "پیش‌نمایش تغییر کالاها" in page and "FIB206-350" in page and "۳۵٬۰۰۰٬۰۰۰" in page
    assert "قیمت پایه (ریال)" in page
    token = re.search(r'name="token" value="([0-9a-f]+)"', page).group(1)
    page = text(c.post("/admin/products/apply", data={"_csrf": tok, "token": token}, follow_redirects=True))
    assert "کالاها به‌روز شد" in page
    assert next(p for p in svc.products() if p["key"] == "FIB206-350")["base_price_rial"] == 35000000
    page = text(c.get("/admin/audit"))
    assert "ورود کالاها" in page and "ساخت کاربر" in page


def test_account_password_change_and_health(env):
    app, svc, _ = env
    c = app.test_client()
    tok = login(c, "sales", "sales-pass-1")
    page = text(c.post("/account", data={"_csrf": tok, "current": "wrong", "new": "x" * 9, "repeat": "x" * 9},
                       follow_redirects=True))
    assert "رمز فعلی درست نیست" in page
    page = text(c.post("/account", data={"_csrf": tok, "current": "sales-pass-1", "new": "new-pass-99",
                                         "repeat": "new-pass-99"}, follow_redirects=True))
    assert "رمز عبور عوض شد" in page and svc.verify_login("sales", "new-pass-99")
    assert c.get("/health").get_json()["ok"] is True


def test_role_change_ends_session(env):
    app, svc, _ = env
    c = app.test_client()
    login(c, "finance", "finance-pass")
    assert c.get("/").status_code == 200
    svc.update_user(svc.user_id_by_name("finance"), "admin", role="viewer")
    assert c.get("/").status_code == 302


def test_pricetoday_source_on_pages(tmp_path):
    cfg = config.load_config(write_settings(tmp_path, provider="pricetoday"))
    svc = Service(cfg)
    at(2026, 11, 1, 9, 30)
    svc.init()
    svc.create_user("admin", "admin-pass-1", "admin", "مدیر سامانه", "test")
    app = create_app(cfg, svc)
    app.config["TESTING"] = True
    c = app.test_client()
    tok = login(c, "admin", "admin-pass-1")
    page = text(c.get("/"))
    assert "دریافت نرخ از priceto.day" in page and "الان‌چند" not in page
    page = text(c.post("/fetch", data={"_csrf": tok}, follow_redirects=True))
    assert "۲۷۰٬۵۰۰" in page and "تأیید لازم دارد" in page
    page = text(c.get("/admin/settings"))
    assert "priceto.day" in page and "https://api.priceto.day/v1/latest/irr/usd" in page
    assert "کلید یا توکن لازم ندارد" in page and "توکن</dt>" not in page
    page = text(c.get("/rates"))
    assert "فایل آزمون" in page
