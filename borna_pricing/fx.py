from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from . import __version__, fmt

USER_AGENT = f"BornaPricing/{__version__} (+internal pricing; hourly)"
MAX_BYTES = 2_000_000
LABELS = {"pricetoday": "priceto.day", "alanchand": "الان‌چند"}
_LATIN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_FRACTION = re.compile(r"\.(\d+)")


@dataclass
class FetchResult:
    ok: bool
    message: str = ""
    value_toman: int | None = None
    source_updated_raw: str | None = None
    source_updated_utc: datetime | None = None
    raw_entry: str | None = None
    http_status: int | None = None
    reached: bool = False


@dataclass
class GuardDecision:
    status: str
    accepted: bool
    message: str
    jump: Decimal | None = None
    age_minutes: float | None = None


def label(cfg) -> str:
    return LABELS.get(cfg.source.provider, cfg.source.provider)


def provider_cfg(cfg):
    return cfg.alanchand if cfg.source.provider == "alanchand" else cfg.pricetoday


def secret(cfg) -> str:
    return cfg.alanchand.token if cfg.source.provider == "alanchand" else ""


def mask(text: str, token: str) -> str:
    if token and len(token) >= 4:
        text = text.replace(token, "***")
        text = text.replace(urllib.parse.quote(token, safe=""), "***")
    return text


def build_url(cfg) -> str:
    if cfg.source.provider == "alanchand":
        a = cfg.alanchand
        sep = "&" if "?" in a.url else "?"
        return a.url + sep + urllib.parse.urlencode({"type": a.type, "token": a.token})
    return cfg.pricetoday.url


def series(cfg) -> tuple[str, str]:
    if cfg.source.provider == "alanchand":
        return cfg.alanchand.slug, cfg.alanchand.field
    tail = urllib.parse.urlparse(cfg.pricetoday.url).path.rstrip("/").rsplit("/", 1)[-1]
    return tail or "usd", "price"


def parse_number(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(round(value))
    s = str(value).translate(_LATIN).replace(",", "").replace("٬", "").strip()
    try:
        return int(round(float(s)))
    except ValueError:
        return None


def parse_utc(raw) -> datetime | None:
    if raw in (None, ""):
        return None
    s = str(raw).strip()
    if s[-1:] in ("Z", "z"):
        s = s[:-1] + "+00:00"
    s = _FRACTION.sub(lambda m: "." + (m.group(1) + "000000")[:6], s, count=1)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_toman(value: int, unit: str) -> int:
    return value if unit == "toman" else int(round(value / 10))


def parse_payload(text: str, cfg) -> FetchResult:
    try:
        data = json.loads(text)
    except ValueError:
        return FetchResult(False, "پاسخ API قالب JSON ندارد.")
    if cfg.source.provider == "alanchand":
        return _parse_alanchand(data, cfg.alanchand)
    return _parse_pricetoday(data, cfg.pricetoday)


def _parse_alanchand(data, a) -> FetchResult:
    if not isinstance(data, dict):
        return FetchResult(False, "پاسخ API قالب مورد انتظار را ندارد.")
    entry = data.get(a.slug)
    if not isinstance(entry, dict):
        if "error" in data:
            return FetchResult(False, f"API خطا داد: «{data.get('error')}»")
        return FetchResult(False, f"نماد «{a.slug}» در پاسخ API نیست (نوع درخواست: {a.type}).")
    value = parse_number(entry.get(a.field))
    if value is None or value <= 0:
        return FetchResult(False, f"فیلد «{a.field}» برای «{a.slug}» عدد معتبر نیست: {entry.get(a.field)!r}")
    raw_upd = entry.get("updated_at")
    upd_utc = None
    if raw_upd:
        try:
            src_tz = timezone(timedelta(minutes=a.source_utc_offset_minutes))
            upd_utc = datetime.strptime(str(raw_upd).strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=src_tz)
            upd_utc = upd_utc.astimezone(timezone.utc)
        except ValueError:
            upd_utc = None
    return FetchResult(True, "", _to_toman(value, a.unit), str(raw_upd) if raw_upd else None, upd_utc,
                       json.dumps(entry, ensure_ascii=False), 200, True)


def _parse_pricetoday(data, p) -> FetchResult:
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        return FetchResult(False, "پاسخ API قالب مورد انتظار را ندارد.")
    if data.get("success") is False or "price" not in data:
        why = data.get("message") or data.get("error") or "نرخی در پاسخ نیست"
        return FetchResult(False, f"API خطا داد: «{why}»")
    value = parse_number(data.get("price"))
    if value is None or value <= 0:
        return FetchResult(False, f"فیلد «price» عدد معتبر نیست: {data.get('price')!r}")
    raw_upd = data.get("time")
    return FetchResult(True, "", _to_toman(value, p.unit), str(raw_upd) if raw_upd else None, parse_utc(raw_upd),
                       json.dumps(data, ensure_ascii=False), 200, True)


def _http_get(url: str, timeout: int) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        body = resp.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError("پاسخ API بیش از حد بزرگ است.")
        return resp.status, body.decode("utf-8", errors="replace")


def fetch(cfg, http_get=None) -> FetchResult:
    src, name = cfg.source, label(cfg)
    if src.mode == "off":
        return FetchResult(False, "دریافت خودکار خاموش است (در settings.ini، بخش [source]: mode = off). "
                                  "نرخ را دستی وارد کنید.")
    if src.mode == "file":
        try:
            text = provider_cfg(cfg).sample_file.read_text(encoding="utf-8-sig")
        except OSError as exc:
            return FetchResult(False, f"فایل نمونه خوانده نشد: {exc}")
        res = parse_payload(text, cfg)
        res.reached = True
        res.http_status = 200
        if res.ok:
            res.message = "حالت آزمون: از فایل نمونه خوانده شد، نه از اینترنت."
        return res
    if src.provider == "alanchand" and not cfg.alanchand.token_is_set:
        return FetchResult(False, "توکن الان‌چند در settings.ini تنظیم نشده است (بخش [alanchand]، کلید token).")
    get = http_get or _http_get
    url, hide = build_url(cfg), secret(cfg)
    try:
        status, text = get(url, src.timeout_seconds)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(MAX_BYTES).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        res = parse_payload(body, cfg) if body else FetchResult(False, "")
        msg = f"API {name} پاسخ HTTP {exc.code} داد."
        if not res.ok and res.message.startswith("API خطا داد"):
            msg += " " + res.message
        if exc.code == 429:
            msg += (" احتمالاً از سقف یک درخواست در ساعت طرح رایگان بیشتر شده است." if src.provider == "alanchand"
                    else " درخواست‌ها زیاد بوده؛ در settings.ini فاصله دریافت را بیشتر کنید.")
        return FetchResult(False, mask(msg, hide), http_status=exc.code, reached=True)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return FetchResult(False, mask(f"اتصال به سرور {name} برقرار نشد: {reason}", hide))
    res = parse_payload(text, cfg)
    res.http_status, res.reached = status, True
    res.message = mask(res.message, hide)
    return res


def evaluate(res: FetchResult, ref_toman: int | None, ref_label: str, now_utc: datetime, gcfg,
             hours=None) -> GuardDecision:
    if not res.ok:
        return GuardDecision("error", False, res.message or "دریافت نرخ ناموفق بود.")
    v = res.value_toman
    if v < gcfg.min_rate_toman or v > gcfg.max_rate_toman:
        return GuardDecision("out_of_range", False,
                             f"نرخ {fmt.money(v)} تومان بیرون از بازه مجاز ({fmt.money(gcfg.min_rate_toman)} تا "
                             f"{fmt.money(gcfg.max_rate_toman)} تومان) است؛ شاید واحد (ریال/تومان) اشتباه است.")
    notes = [res.message] if res.message else []
    age = None
    if res.source_updated_utc is None:
        status_if_ok, why = "held", "زمان به‌روزرسانی منبع معلوم نیست؛ تأیید دستی لازم است."
    else:
        upd = res.source_updated_utc
        wall = (now_utc - upd).total_seconds() / 60
        age = hours.open_minutes_between(upd, now_utc) if hours else wall
        if age > gcfg.max_age_minutes:
            span = "دقیقه از ساعت کاری" if hours else "دقیقه"
            return GuardDecision("stale", False,
                                 f"منبع از {fmt.when(upd, now_utc)} به‌روز نشده است ({fmt.fa(int(age))} {span}؛ "
                                 f"سقف {fmt.fa(gcfg.max_age_minutes)} دقیقه). از این نرخ استفاده نشد.",
                                 age_minutes=age)
        if wall < -15:
            notes.append("زمان منبع جلوتر از ساعت این سرور است؛ ساعت و منطقه زمانی سرور را بررسی کنید.")
        status_if_ok, why = "ok", ""
    jump = None
    if ref_toman:
        jump = Decimal(v) / Decimal(ref_toman) - 1
        if abs(jump) > gcfg.jump_threshold:
            status_if_ok = "held"
            why = (f"این نرخ {fmt.pct_words(jump)} از {ref_label} ({fmt.money(ref_toman)} تومان) است و از سقف جهش "
                   f"{fmt.pct_plain_words(gcfg.jump_threshold)} می‌گذرد؛ تا تأیید، قیمت‌ها با آن حساب نمی‌شوند.")
    if why:
        notes.insert(0, why)
    return GuardDecision(status_if_ok, status_if_ok == "ok", " ".join(n for n in notes if n), jump, age)
