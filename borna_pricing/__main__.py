from __future__ import annotations

import argparse
import getpass
import logging
import os
import socket
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__, config, fmt, fx
from .clock import now_utc
from .hours import BusinessHours
from .service import RATE_SOURCE, ROLES, Service, ServiceError, copy_example_settings


def _console_utf8():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def setup_logging(cfg) -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    form = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg.paths.log_file.parent.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(cfg.paths.log_file, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(form)
    root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(form)
    root.addHandler(sh)
    logging.getLogger("waitress.queue").setLevel(logging.ERROR)


def _password(given: str | None, prompt: str) -> str:
    if given:
        return given
    while True:
        p1 = getpass.getpass(f"{prompt} (دست‌کم ۸ نویسه): ")
        p2 = getpass.getpass("تکرار رمز: ")
        if p1 == p2 and len(p1) >= 8:
            return p1
        print("دو رمز یکی نیست یا کوتاه است؛ دوباره.")


def _lan_ips() -> list[str]:
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


def _source_note(cfg) -> str | None:
    if cfg.source.mode != "live":
        return f"حالت دریافت نرخ «{cfg.source.mode}» است، نه live."
    if cfg.source.provider == "alanchand" and not cfg.alanchand.token_is_set:
        return "توکن الان‌چند تنظیم نشده؛ تا آن را در settings.ini (بخش [alanchand]) نگذارید، فقط نرخ دستی کار می‌کند."
    return None


def cmd_init(args) -> int:
    root = config.default_settings_path().parent
    created = copy_example_settings(root)
    if created:
        print(f"فایل تنظیمات ساخته شد: {created}")
    cfg = config.load_config()
    print(f"منبع نرخ دلار: {fx.label(cfg)}")
    note = _source_note(cfg)
    if note:
        print("  توجه:", note)
    setup_logging(cfg)
    from .web import ensure_secret_key
    ensure_secret_key(cfg.paths.secret_key_file)
    svc = Service(cfg)
    out = svc.init(user="setup")
    if out["imported"]:
        print(f"کالاها وارد شد: {out['imported']['total']} کالا از {cfg.paths.products_xlsx.name}")
        for w in out["imported"].get("warnings", []):
            print("  هشدار:", w)
    print(f"لیست پایه (تاریخ {cfg.pricing.base_date_text}، دلار {cfg.pricing.base_rate_toman:,} تومان) آماده است.")
    if svc.user_count() == 0:
        name = args.admin or "admin"
        pw = _password(args.password, f"رمز کاربر مدیر «{name}»")
        svc.create_user(name, pw, "admin", "مدیر سامانه", "setup")
        print(f"کاربر مدیر «{name}» ساخته شد.")
    print(f"پایگاه‌داده: {cfg.paths.database}")
    if os.name == "nt":
        print("قدم بعد: test_api_windows.bat را اجرا کنید، سپس start_windows.bat")
    else:
        print("قدم بعد: python -m borna_pricing test-api   سپس   python -m borna_pricing serve")
    return 0


def cmd_test_api(args) -> int:
    cfg = config.load_config()
    src = cfg.source
    where = fx.mask(fx.build_url(cfg), fx.secret(cfg)) if src.mode == "live" else fx.provider_cfg(cfg).sample_file
    print(f"منبع: {fx.label(cfg)} | حالت: {src.mode} | نشانی: {where}")
    res = fx.fetch(cfg)
    if not res.ok:
        print("ناموفق:", res.message)
        if res.http_status:
            print("کد HTTP:", res.http_status)
        return 1
    slug, field = fx.series(cfg)
    print(f"موفق. {slug}/{field} = {res.value_toman:,} تومان (= {res.value_toman * 10:,} ریال)")
    when = fmt.jdatetime(res.source_updated_utc) + " (تهران)" if res.source_updated_utc else "نامعلوم"
    print(f"زمان به‌روزرسانی منبع: {when}")
    d = fx.evaluate(res, None, "", now_utc(), cfg.guards, BusinessHours(cfg.hours))
    print(f"کنترل تازگی و بازه: {d.status} {d.message}")
    print("این آزمون در پایگاه‌داده ثبت نشد.")
    if src.provider == "alanchand":
        print("یادتان باشد طرح رایگان الان‌چند یک درخواست در ساعت است.")
    return 0


def cmd_fetch_now(args) -> int:
    svc = Service(config.load_config())
    r = svc.fetch_now(args.user, force=args.force)
    print(f"وضعیت: {r['status']} | نرخ: {r['value_toman']} تومان | {r['message']}")
    if r["run_id"]:
        print(f"محاسبه شماره {r['run_id']} انجام شد.")
    return 0 if r["status"] in ("ok", "held") else 1


def cmd_set_rate(args) -> int:
    svc = Service(config.load_config())
    r = svc.manual_rate(args.toman, args.user, args.note)
    print(f"نرخ دستی ثبت شد (شماره {r['rate_id']})؛ محاسبه شماره {r['run_id']}.")
    return 0


def cmd_status(args) -> int:
    cfg = config.load_config()
    svc = Service(cfg)
    if not svc.initialized():
        print("سامانه هنوز راه‌اندازی نشده؛ اول: python -m borna_pricing init")
        return 1
    st = svc.state()
    la, at = st["last_accepted"], st["last_attempt"]
    print(f"اکنون: {fmt.jdatetime(st['now'])} | ساعت کاری: {'بله' if st['is_open'] else 'خیر'}")
    if la:
        print(f"آخرین نرخ پذیرفته‌شده: {la['value_toman']:,} تومان "
              f"({RATE_SOURCE.get(la['source'], la['source'])}، {fmt.jdatetime(la['fetched_at'])})")
    else:
        print("هنوز نرخی پذیرفته نشده.")
    if at:
        print(f"آخرین تلاش: {at['status']} — {at['message'] or ''}")
    if st["held"]:
        print(f"در انتظار تأیید: {st['held']['value_toman']:,} تومان — {st['held']['message']}")
    pub = st["publication"]
    if pub:
        print(f"لیست جاری: شماره {pub['id']} ({pub['kind']})، دلار {pub['rate_rial'] // 10:,} تومان، "
              f"{'معتبر تا ' + fmt.jdatetime(pub['valid_until_eff']) if pub['is_valid'] else 'منقضی'}")
    run = st["run"]
    if run:
        print(f"آخرین محاسبه: شماره {run['id']} — {run['status']} — انتشار لازم: {bool(run['needs_publish'])}"
              f"، تأیید لازم: {bool(run['needs_approval'])}")
    if st["next_auto_fetch"]:
        print(f"دریافت خودکار بعدی: {fmt.jdatetime(st['next_auto_fetch'])}")
    return 0


def cmd_create_user(args) -> int:
    svc = Service(config.load_config())
    pw = _password(args.password, f"رمز کاربر «{args.username}»")
    svc.create_user(args.username, pw, args.role, args.name or "", "cli")
    print(f"کاربر «{args.username}» با نقش {args.role} ساخته شد.")
    return 0


def cmd_set_password(args) -> int:
    svc = Service(config.load_config())
    uid = svc.user_id_by_name(args.username)
    if uid is None:
        print("کاربر پیدا نشد.")
        return 1
    svc.update_user(uid, "cli", password=_password(args.password, f"رمز تازه «{args.username}»"))
    print("رمز عوض شد.")
    return 0


def cmd_import(args) -> int:
    cfg = config.load_config()
    svc = Service(cfg)
    path = Path(args.path) if args.path else cfg.paths.products_xlsx
    d = svc.import_products(path, args.user)
    print(f"{d['total']} کالا؛ جدید: {len(d['added'])}، تغییر: {len(d['changed'])}، حذف: {len(d['removed'])}")
    for w in d.get("warnings", []):
        print("  هشدار:", w)
    return 0


def cmd_backup(args) -> int:
    svc = Service(config.load_config())
    print("پشتیبان:", svc.backup(Path(args.dest) if args.dest else None))
    return 0


def cmd_serve(args) -> int:
    cfg = config.load_config()
    setup_logging(cfg)
    svc = Service(cfg)
    if not svc.initialized():
        print("سامانه هنوز راه‌اندازی نشده؛ اول: python -m borna_pricing init")
        return 1
    migrated = svc.ensure_schema()
    if migrated:
        print(f"پایگاه‌داده به نسخه {migrated[-1]} به‌روز شد (پشتیبان پیش از آن در data/backups ساخته شد).")
    from waitress import serve

    from .scheduler import Scheduler
    from .web import create_app
    app = create_app(cfg, svc)
    host, port = args.host or cfg.web.host, args.port or cfg.web.port
    sched = None
    if not args.no_scheduler:
        sched = Scheduler(svc)
        sched.start()
    print(f"سامانه قیمت‌گذاری برنا {__version__} روشن شد:  http://127.0.0.1:{port}/")
    if host in ("0.0.0.0", "::"):
        for ip in _lan_ips():
            print(f"  از رایانه‌های دیگر شبکه:  http://{ip}:{port}/")
    print(f"  منبع نرخ دلار: {fx.label(cfg)}")
    note = _source_note(cfg)
    if note:
        print("  توجه:", note)
    print("برای خاموش کردن: Ctrl+C")
    try:
        serve(app, host=host, port=port, threads=8, ident="borna-pricing")
    finally:
        if sched:
            sched.stop()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m borna_pricing",
                                description="سامانه قیمت‌گذاری ارزی برنا سنسور")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="راه‌اندازی اولیه: تنظیمات، پایگاه‌داده، کالاها، لیست پایه، کاربر مدیر")
    s.add_argument("--admin", help="نام کاربری مدیر (پیش‌فرض admin)")
    s.add_argument("--password", help="رمز مدیر (اگر ندهید پرسیده می‌شود)")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("test-api", help="آزمون اتصال به منبع نرخ دلار (چیزی ذخیره نمی‌کند)")
    s.set_defaults(func=cmd_test_api)

    s = sub.add_parser("fetch-now", help="دریافت نرخ همین حالا و محاسبه قیمت‌ها")
    s.add_argument("--force", action="store_true", help="بی‌توجه به فاصله دو دریافت")
    s.add_argument("--user", default="cli")
    s.set_defaults(func=cmd_fetch_now)

    s = sub.add_parser("set-rate", help="ثبت نرخ دستی (تومان)")
    s.add_argument("toman", type=int)
    s.add_argument("--note", required=True, help="منبع یا دلیل")
    s.add_argument("--user", default="cli")
    s.set_defaults(func=cmd_set_rate)

    s = sub.add_parser("status", help="خلاصه وضعیت")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("create-user", help="ساخت کاربر")
    s.add_argument("username")
    s.add_argument("--role", choices=ROLES, required=True)
    s.add_argument("--name", help="نام و نام خانوادگی")
    s.add_argument("--password")
    s.set_defaults(func=cmd_create_user)

    s = sub.add_parser("set-password", help="تغییر رمز یک کاربر")
    s.add_argument("username")
    s.add_argument("--password")
    s.set_defaults(func=cmd_set_password)

    s = sub.add_parser("import-products", help="ورود دوباره کالاها از Excel")
    s.add_argument("path", nargs="?")
    s.add_argument("--user", default="cli")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("backup", help="پشتیبان‌گیری از پایگاه‌داده")
    s.add_argument("dest", nargs="?")
    s.set_defaults(func=cmd_backup)

    s = sub.add_parser("serve", help="روشن کردن وب‌سایت داخلی و زمان‌بند")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.add_argument("--no-scheduler", action="store_true", help="بدون دریافت خودکار نرخ")
    s.set_defaults(func=cmd_serve)
    return p


def main(argv=None) -> int:
    _console_utf8()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (config.ConfigError, ServiceError) as exc:
        print("خطا:", exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
