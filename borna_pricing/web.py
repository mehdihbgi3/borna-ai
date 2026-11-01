from __future__ import annotations

import os
import secrets
import threading
import time
from datetime import timedelta
from decimal import Decimal
from functools import wraps
from io import BytesIO
from pathlib import Path

from markupsafe import Markup
from flask import (Flask, abort, flash, g, jsonify, redirect, render_template, request, send_file, session,
                   url_for)

from . import __version__, charts, fmt, fx, pricelist
from .clock import iso, now_utc, parse_iso
from .service import (PUB_KIND, RATE_SOURCE, RATE_STATUS, ROLE_LABELS, ROLES, RUN_STATUS, Service,
                      ServiceError)

ROLE_RANK = {"viewer": 1, "approver": 2, "admin": 3}
ROLE_SHORT = {"viewer": "فروش", "approver": "تأییدکننده", "admin": "مدیر سامانه"}
SPECIAL_USERS = {"setup": "راه‌اندازی سامانه", "system": "سامانه (خودکار)", "cli": "خط فرمان"}
_LATIN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

FIELD_LABELS = {
    "row_no": "ردیف", "status": "وضعیت تطبیق", "name": "نام در لیست قیمت", "catalog_name": "نام در کاتالوگ",
    "part_no": "پارت‌نامبر", "design": "طرح", "ecu": "ECU", "vehicle": "خودرو", "length_text": "طول سیم",
    "socket_color": "رنگ سوکت", "wire_color": "رنگ سیم", "base_price_rial": "قیمت پایه (ریال)",
    "moq": "حداقل سفارش", "alpha": "α", "note": "یادداشت",
}
AUDIT_LABELS = {
    "login": "ورود", "login_failed": "ورود ناموفق", "rate_fetch": "دریافت نرخ", "rate_approve": "تأیید نرخ",
    "rate_reject": "رد نرخ", "rate_manual": "نرخ دستی", "run": "محاسبه قیمت", "publish": "انتشار لیست",
    "renew": "تمدید اعتبار", "base_publication": "لیست پایه", "products_import": "ورود کالاها",
    "user_create": "ساخت کاربر", "user_update": "تغییر کاربر", "schema_migrate": "به‌روزرسانی پایگاه‌داده",
    "source_create": "ساخت منبع بازار", "source_update": "تغییر منبع بازار", "source_policy": "مجوز پایش منبع",
    "observation_manual": "ثبت دستی قیمت رقیب", "observation_approve": "تأیید قیمت رقیب",
    "observation_reject": "رد قیمت رقیب", "match_approve": "تأیید تطبیق", "match_reject": "رد تطبیق",
    "match_manual": "تطبیق دستی", "match_unlink": "لغو تطبیق", "match_bulk_approve": "تأیید گروهی تطبیق",
    "listing_tier": "تعیین اصالت آگهی", "listing_own": "آگهی خود برنا", "listing_changed": "تغییر آگهی تطبیق‌شده",
    "spec_set": "ثبت مشخصه فنی", "spec_remove": "حذف مشخصه فنی", "agents_pause": "توقف ایجنت‌ها",
    "agents_resume": "روشن شدن ایجنت‌ها",
}


AUDIT_KEYS = {
    "rate_id": "شماره نرخ", "run_id": "شماره محاسبه", "publication_id": "شماره لیست", "kind": "نوع",
    "rate_toman": "دلار (تومان)", "value_toman": "نرخ (تومان)", "valid_until": "اعتبار تا", "note": "یادداشت",
    "status": "وضعیت", "message": "توضیح", "needs_publish": "انتشار لازم", "needs_approval": "تأیید لازم",
    "n_publish": "کالای بالای آستانه انتشار", "n_approval": "کالای بالای آستانه تأیید", "username": "کاربر",
    "role": "نقش", "file": "فایل", "total": "تعداد کالا", "added": "جدید", "removed": "حذف", "changed": "تغییر",
    "reactivated": "دوباره فعال", "date": "تاریخ", "products": "تعداد کالا", "active": "فعال", "password": "رمز",
    "full_name": "نام", "versions": "نسخه", "source": "منبع", "domain": "دامنه", "policy_reset": "مجوز از نو",
    "observation": "شماره مشاهده", "seller": "فروشنده", "product": "کالا", "listing": "شماره آگهی",
    "count": "تعداد", "tier": "اصالت", "own": "مال برنا", "attr": "ویژگی", "value": "مقدار", "old": "قبلی",
    "new": "تازه",
}


def audit_value(key, value):
    if isinstance(value, bool):
        return "بله" if value else "خیر"
    if key == "status":
        return RATE_STATUS.get(value, value)
    if key == "kind":
        return PUB_KIND.get(value, value)
    if key == "role":
        return ROLE_LABELS.get(value, value)
    if key == "valid_until" and isinstance(value, str):
        return fmt.jdatetime(value)
    if key in ("rate_toman", "value_toman") and isinstance(value, int):
        return fmt.money(value)
    if isinstance(value, list):
        return "، ".join(str(v) for v in value) if value else "—"
    return value


def ensure_secret_key(path) -> str:
    path = Path(path)
    if path.exists():
        key = path.read_text(encoding="utf-8").strip()
        if len(key) >= 32:
            return key
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    path.write_text(key, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


class LoginThrottle:

    def __init__(self, max_fail: int = 5, window: int = 300):
        self.max_fail, self.window = max_fail, window
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def wait_seconds(self, key: str) -> int:
        with self._lock:
            now = time.monotonic()
            fails = [t for t in self._fails.get(key, []) if now - t < self.window]
            self._fails[key] = fails
            if len(fails) >= self.max_fail:
                return int(self.window - (now - fails[0])) + 1
            return 0

    def fail(self, key: str) -> None:
        with self._lock:
            self._fails.setdefault(key, []).append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)


def parse_toman(text: str) -> int:
    s = (text or "").translate(_LATIN).replace(",", "").replace("٬", "").replace(" ", "").strip()
    if not s.isdigit():
        raise ServiceError("نرخ را فقط با رقم و به تومان وارد کنید؛ مثلاً 273400.")
    return int(s)


def meter_vm(change: Decimal | None, held: Decimal | None, pub_thr: Decimal, appr_thr: Decimal) -> dict:
    scale = max(Decimal("0.08"), appr_thr * Decimal("1.6"))

    def pos(x):
        x = max(-scale, min(scale, x))
        return float((x + scale) / (2 * scale) * 100)

    def zone(x):
        if x is None:
            return "none"
        return "approval" if abs(x) > appr_thr else "publish" if abs(x) > pub_thr else "calm"

    return {
        "scale": scale, "change": change, "held": held,
        "pos": pos(change) if change is not None else None,
        "held_pos": pos(held) if held is not None else None,
        "clamped": change is not None and abs(change) > scale,
        "zone": zone(change),
        "zones": [
            ("approval", pos(-scale), pos(-appr_thr)), ("publish", pos(-appr_thr), pos(-pub_thr)),
            ("calm", pos(-pub_thr), pos(pub_thr)), ("publish", pos(pub_thr), pos(appr_thr)),
            ("approval", pos(appr_thr), pos(scale))],
        "ticks": [(pos(x), fmt.pct(x, digits=0) if x else "۰") for x in (-appr_thr, -pub_thr, Decimal(0), pub_thr, appr_thr)],
        "pub_thr": pub_thr, "appr_thr": appr_thr,
    }


def table_vm(st: dict, approver: bool) -> dict:
    pub, run = st["publication"], st["run"]
    show_run = bool(approver and run and run["is_current"] and run["status"] in ("draft", "no_change"))
    rows = []
    if show_run:
        for r in run["rows"]:
            chg = Decimal(r["change_vs_pub"]) if r["change_vs_pub"] is not None else None
            rows.append({"row_no": r["row_no"], "key": r["product_key"], "name": r["name"], "part_no": r["part_no"],
                         "moq": r["moq"], "status": r["product_status"], "pub": r["pub_price_rial"],
                         "new": r["price_rial"], "chg": chg, "flag_publish": r["flag_publish"],
                         "flag_approval": r["flag_approval"], "changed": r["price_rial"] != r["pub_price_rial"]})
    elif pub:
        for r in pub["rows"]:
            rows.append({"row_no": r["row_no"], "key": r["product_key"], "name": r["name"], "part_no": r["part_no"],
                         "moq": r["moq"], "pub": r["price_rial"]})
    return {"rows": rows, "compare": show_run, "removed": (run or {}).get("removed", []) if show_run else []}


def create_app(cfg, svc: Service | None = None, market=None) -> Flask:
    svc = svc or Service(cfg)
    if market is None:
        from .market.service import MarketService
        market = MarketService(svc)
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=ensure_secret_key(cfg.paths.secret_key_file),
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_NAME="borna_session",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=cfg.web.session_hours),
        MAX_CONTENT_LENGTH=10 * 1024 * 1024, JSON_AS_ASCII=False)
    if cfg.web.behind_https_proxy:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
        app.config["SESSION_COOKIE_SECURE"] = True
    app.extensions["borna_service"] = svc
    app.extensions["borna_market"] = market
    throttle = LoginThrottle()
    uploads = Path(cfg.paths.database).parent / "uploads"

    env = app.jinja_env
    env.filters.update(money=fmt.money, pct=fmt.pct, pctp=fmt.pct_plain, pctw=fmt.pct_plain_words,
                       pctx=lambda x: Markup(f'<span class="n">{fmt.pct(x)}</span>'), pctwords=fmt.pct_words, fa=fmt.fa, jdt=fmt.jdatetime, jdate=fmt.jdate, hm=fmt.hm,
                       weekday=fmt.weekday, jlong=fmt.jdate_long, when=fmt.when,
                       toman=lambda rial: fmt.money(int(rial) // 10) if rial not in (None, "") else "—",
                       dec=lambda s: Decimal(s) if s not in (None, "") else None)
    env.globals.update(RATE_STATUS=RATE_STATUS, RATE_SOURCE=RATE_SOURCE, PUB_KIND=PUB_KIND, RUN_STATUS=RUN_STATUS,
                       ROLE_LABELS=ROLE_LABELS, ROLES=ROLES, AUDIT_LABELS=AUDIT_LABELS, FIELD_LABELS=FIELD_LABELS, ROLE_SHORT=ROLE_SHORT, AUDIT_KEYS=AUDIT_KEYS,
                       audit_value=audit_value, source_label=fx.label(cfg),
                       version=__version__)
    from .market import web as market_web
    env.globals.update(market_web.globals_for_templates())

    def csrf_token() -> str:
        if "_csrf" not in session:
            session["_csrf"] = secrets.token_urlsafe(32)
        return session["_csrf"]

    env.globals["csrf_token"] = csrf_token

    def can(role: str) -> bool:
        u = g.get("user")
        return bool(u) and ROLE_RANK[u["role"]] >= ROLE_RANK[role]

    @app.context_processor
    def _inject():
        names = {}

        def who(username):
            if not username:
                return "—"
            if username in SPECIAL_USERS:
                return SPECIAL_USERS[username]
            if not names:
                names.update({u["username"].lower(): (u["full_name"] or u["username"]) for u in svc.users()})
            return names.get(username.lower(), username)
        return {"cfg": cfg, "user": g.get("user"), "csrf_token": csrf_token, "can": can, "now": now_utc(), "who": who}

    @app.before_request
    def _load_user():
        g.user = None
        uid = session.get("uid")
        if uid:
            u = svc.get_user(uid)
            if u and u["active"] and session.get("role") == u["role"]:
                g.user = u
            else:
                session.clear()
        if request.method == "POST":
            sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
            if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
                abort(400, "نشست شما منقضی شده است؛ صفحه را تازه کنید و دوباره امتحان کنید.")

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                "script-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
        if request.endpoint not in ("static",):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    def need(role: str = "viewer"):
        def deco(fn):
            @wraps(fn)
            def wrapper(*a, **kw):
                if not g.get("user"):
                    nxt = request.full_path if request.method == "GET" else "/"
                    return redirect(url_for("login", next=nxt.rstrip("?")))
                if not can(role):
                    abort(403)
                return fn(*a, **kw)
            return wrapper
        return deco

    def uname() -> str:
        return g.user["username"]

    def back(default="dashboard"):
        return redirect(url_for(default))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(url_for("dashboard"))
        error = None
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            key = f"{request.remote_addr}|{username.lower()}"
            wait = throttle.wait_seconds(key)
            if wait:
                error = f"چند بار رمز اشتباه وارد شد؛ {fmt.fa(wait // 60 + 1)} دقیقه دیگر دوباره امتحان کنید."
            else:
                u = svc.verify_login(username, request.form.get("password") or "")
                if u:
                    throttle.reset(key)
                    session.clear()
                    session.permanent = True
                    session["uid"], session["role"] = u["id"], u["role"]
                    session["_csrf"] = secrets.token_urlsafe(32)
                    nxt = request.args.get("next") or ""
                    return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard"))
                throttle.fail(key)
                error = "نام کاربری یا رمز عبور درست نیست."
        return render_template("login.html", error=error), (401 if error else 200)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/account", methods=["GET", "POST"])
    @need()
    def account():
        if request.method == "POST":
            cur, new, rep = (request.form.get(k) or "" for k in ("current", "new", "repeat"))
            try:
                if not svc.check_password(uname(), cur):
                    raise ServiceError("رمز فعلی درست نیست.")
                if new != rep:
                    raise ServiceError("رمز تازه و تکرار آن یکی نیست.")
                svc.update_user(g.user["id"], uname(), password=new)
                flash("رمز عبور عوض شد.", "ok")
                return redirect(url_for("account"))
            except ServiceError as exc:
                flash(str(exc), "error")
        return render_template("account.html")

    @app.get("/")
    @need()
    def dashboard():
        st = svc.state()
        approver = can("approver")
        held_change = None
        if st["held"] and st["publication"] and st["held"]["value_toman"]:
            held_change = Decimal(st["held"]["value_toman"] * 10) / Decimal(st["publication"]["rate_rial"]) - 1
        meter = meter_vm(st.get("change_vs_published"), held_change, cfg.pricing.publish_threshold,
                         cfg.pricing.approval_threshold)
        run = st["run"]
        run_live = bool(run and run["is_current"] and st["last_accepted"] and run["rate_id"] == st["last_accepted"]["id"])
        last_attempt = st["last_attempt"]
        attempt_problem = last_attempt if (last_attempt and last_attempt["status"] in ("error", "stale", "out_of_range")
                                           and (not st["last_accepted"] or last_attempt["id"] > st["last_accepted"]["id"])) else None
        return render_template("dashboard.html", st=st, meter=meter, table=table_vm(st, approver), run=run,
                               run_live=run_live, attempt_problem=attempt_problem,
                               fetch_ready=st["fetch_allowed_at"] <= st["now"],
                               age_min=lambda iso_s: (st["now"] - parse_iso(iso_s)).total_seconds() / 60 if iso_s else None,
                               ago=lambda iso_s: fmt.ago((st["now"] - parse_iso(iso_s)).total_seconds()) if iso_s else "")

    def act(fn, ok_msg):
        try:
            res = fn()
            flash(ok_msg(res) if callable(ok_msg) else ok_msg, "ok")
        except ServiceError as exc:
            flash(str(exc), "error")
        return back()

    @app.post("/rates/<int:rate_id>/decide")
    @need("approver")
    def decide_rate(rate_id):
        accept = request.form.get("decision") == "accept"
        return act(lambda: svc.decide_held(rate_id, uname(), accept, request.form.get("note", "")),
                   "نرخ پذیرفته شد و قیمت‌ها دوباره محاسبه شد." if accept else "نرخ رد شد؛ قیمت‌ها با آن حساب نمی‌شوند.")

    @app.post("/rates/manual")
    @need("approver")
    def manual_rate():
        return act(lambda: svc.manual_rate(parse_toman(request.form.get("toman")), uname(), request.form.get("note", "")),
                   "نرخ دستی ثبت شد و قیمت‌ها دوباره محاسبه شد.")

    @app.post("/fetch")
    @need("approver")
    def fetch():
        try:
            r = svc.fetch_now(uname())
            kind = "ok" if r["status"] == "ok" else "warn" if r["status"] == "held" else "error"
            msg = {"ok": f"نرخ تازه دریافت شد: {fmt.money(r['value_toman'])} تومان.",
                   "held": f"نرخ {fmt.money(r['value_toman'])} تومان دریافت شد ولی تأیید لازم دارد."}.get(
                r["status"], f"نرخ دریافت نشد: {r['message']}")
            flash(msg, kind)
        except ServiceError as exc:
            flash(str(exc), "error")
        return back()

    @app.post("/recompute")
    @need("approver")
    def recompute():
        return act(lambda: svc.recompute(uname()), "قیمت‌ها با آخرین نرخ پذیرفته‌شده دوباره محاسبه شد.")

    @app.post("/runs/<int:run_id>/publish")
    @need("approver")
    def publish(run_id):
        return act(lambda: svc.publish(run_id, uname(), request.form.get("confirm") == "1", request.form.get("note", "")),
                   lambda pid: f"لیست قیمت شماره {fmt.fa(pid)} منتشر شد.")

    @app.post("/renew")
    @need("approver")
    def renew():
        return act(lambda: svc.renew(uname()), "اعتبار لیست فعلی تا پایان روز کاری امروز تمدید شد.")

    def _pub_or_404(pub_id):
        pub = svc.publication(pub_id)
        if pub is None:
            abort(404)
        return pub

    @app.get("/pricelist")
    @app.get("/pricelist/<int:pub_id>")
    @need()
    def pricelist_page(pub_id=None):
        return render_template("pricelist.html", pub=_pub_or_404(pub_id))

    @app.get("/pricelist.xlsx")
    @app.get("/pricelist/<int:pub_id>.xlsx")
    @need()
    def pricelist_xlsx(pub_id=None):
        pub = _pub_or_404(pub_id)
        data = pricelist.build_xlsx(pub, cfg)
        return send_file(BytesIO(data), as_attachment=True, download_name=pricelist.filename(pub),
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.get("/publications")
    @need()
    def publications():
        return render_template("publications.html", pubs=svc.publications())

    @app.get("/rates")
    @need()
    def rates():
        days = request.args.get("days", "30")
        days = int(days) if days in ("7", "30", "90", "365") else 30
        acc = svc.accepted_rates(days)
        chart = charts.line_chart(
            [(parse_iso(r["fetched_at"]), r["value_toman"]) for r in acc],
            value_fmt=lambda v: fmt.money(round(v)), axis_fmt=charts.axis_toman, unit="تومان",
            ref=(cfg.pricing.base_rate_toman, f"نرخ مبنا {fmt.money(cfg.pricing.base_rate_toman)}"),
            extend_to=None)
        return render_template("rates.html", chart=chart, days=days, log=svc.rate_log(300), n_acc=len(acc))

    @app.get("/products/<key>")
    @need()
    def product(key):
        h = svc.product_history(key)
        if h is None:
            abort(404)
        chart = charts.line_chart(
            [(parse_iso(x["published_at"]), x["price_rial"]) for x in h["history"]],
            value_fmt=lambda v: fmt.money(round(v)), axis_fmt=charts.axis_rial_million, unit="ریال",
            step=True, extend_to=now_utc())
        return render_template("product.html", h=h, p=h["product"], chart=chart)

    @app.route("/admin/users", methods=["GET", "POST"])
    @need("admin")
    def admin_users():
        if request.method == "POST":
            f = request.form
            try:
                if f.get("action") == "create":
                    svc.create_user(f.get("username"), f.get("password"), f.get("role"), f.get("full_name"), uname())
                    flash(f"کاربر «{f.get('username')}» ساخته شد.", "ok")
                else:
                    uid = int(f.get("user_id", "0"))
                    svc.update_user(uid, uname(), role=f.get("role") or None,
                                    active=(f.get("active") == "1") if f.get("active") in ("0", "1") else None,
                                    password=f.get("password") or None, full_name=f.get("full_name"))
                    flash("تغییرات کاربر ذخیره شد.", "ok")
            except (ServiceError, ValueError) as exc:
                flash(str(exc), "error")
            return redirect(url_for("admin_users"))
        return render_template("admin_users.html", users=svc.users())

    @app.get("/admin/products")
    @need("admin")
    def admin_products():
        return render_template("admin_products.html", products=svc.products(include_inactive=True))

    @app.post("/admin/products/preview")
    @need("admin")
    def admin_products_preview():
        f = request.files.get("file")
        if not f or not f.filename.lower().endswith((".xlsx", ".xlsm")):
            flash("یک فایل Excel با پسوند xlsx انتخاب کنید.", "error")
            return redirect(url_for("admin_products"))
        uploads.mkdir(parents=True, exist_ok=True)
        for old in uploads.glob("*.xlsx"):
            if time.time() - old.stat().st_mtime > 86400:
                old.unlink(missing_ok=True)
        token = secrets.token_hex(12)
        path = uploads / f"{token}.xlsx"
        f.save(path)
        session["import_token"] = token
        session["import_name"] = os.path.basename(f.filename)[:120]
        try:
            pv = svc.preview_import(path)
        except Exception as exc:
            path.unlink(missing_ok=True)
            flash(f"فایل خوانده نشد: {exc}", "error")
            return redirect(url_for("admin_products"))
        return render_template("admin_import.html", pv=pv, token=token, name=session["import_name"])

    @app.post("/admin/products/apply")
    @need("admin")
    def admin_products_apply():
        token = request.form.get("token", "")
        if not token or token != session.get("import_token"):
            flash("پیش‌نمایش منقضی شده؛ فایل را دوباره بارگذاری کنید.", "error")
            return redirect(url_for("admin_products"))
        path = uploads / f"{token}.xlsx"
        try:
            d = svc.import_products(path, uname())
            flash(f"کالاها به‌روز شد: {fmt.fa(len(d['added']))} جدید، {fmt.fa(len(d['changed']))} تغییر، "
                  f"{fmt.fa(len(d['removed']))} حذف.", "ok")
        except ServiceError as exc:
            flash(str(exc), "error")
        finally:
            path.unlink(missing_ok=True)
            session.pop("import_token", None)
        return redirect(url_for("admin_products"))

    @app.get("/admin/audit")
    @need("admin")
    def admin_audit():
        action = request.args.get("action") or None
        return render_template("admin_audit.html", rows=svc.audit_log(500, action), actions=svc.audit_actions(),
                               action=action)

    @app.get("/admin/settings")
    @need("admin")
    def admin_settings():
        return render_template("admin_settings.html", hours=svc.hours.describe(),
                               holidays=sorted(cfg.hours.holidays))

    from types import SimpleNamespace
    market_web.register(app, svc, market, SimpleNamespace(need=need, uname=uname, can=can))

    @app.get("/health")
    def health():
        return jsonify(ok=True, version=__version__, time=iso(now_utc()))

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    @app.errorhandler(500)
    def _error(exc):
        code = getattr(exc, "code", 500) or 500
        titles = {400: "درخواست نامعتبر", 403: "دسترسی ندارید", 404: "پیدا نشد", 413: "فایل بزرگ است",
                  500: "خطای داخلی"}
        texts = {403: "این بخش برای نقش کاربری شما باز نیست.", 404: "این صفحه یا لیست وجود ندارد.",
                 413: "حجم فایل بیش از ۱۰ مگابایت است.", 500: "خطایی رخ داد و در فایل گزارش ثبت شد."}
        desc = getattr(exc, "description", None) if code == 400 else None
        return render_template("error.html", code=code, title=titles.get(code, "خطا"),
                               text=desc or texts.get(code, "")), code

    return app
