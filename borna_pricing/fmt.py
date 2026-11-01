from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from . import jalali
from .clock import TEHRAN, parse_iso

_FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def fa(value) -> str:
    return str(value).translate(_FA)


def money(n) -> str:
    if n is None or n == "":
        return "—"
    return fa(f"{int(n):,}".replace(",", "٬")).replace("-", "−")


def pct(x, digits: int = 2, signed: bool = True) -> str:
    if x is None:
        return "—"
    v = (Decimal(str(x)) * 100).quantize(Decimal(1).scaleb(-digits))
    if v == 0:
        s = f"{abs(v):.{digits}f}"
    else:
        s = f"{v:+.{digits}f}" if signed else f"{v:.{digits}f}"
    return fa(s.replace("-", "−").replace(".", "٫")) + "٪"


def pct_plain(x) -> str:
    if x is None:
        return "—"
    v = (Decimal(str(x)) * 100).quantize(Decimal("0.01")).normalize()
    s = format(v, "f")
    return fa(s.replace(".", "٫")) + "٪"


def pct_words(x, digits: int = 2) -> str:
    if x is None:
        return "—"
    v = (Decimal(str(x)) * 100).quantize(Decimal(1).scaleb(-digits))
    if v == 0:
        return "بدون تغییر"
    num = fa(f"{abs(v):.{digits}f}".replace(".", "٫"))
    return f"{num} درصد {'بالاتر' if v > 0 else 'پایین‌تر'}"


def pct_plain_words(x) -> str:
    return pct_plain(x).replace("٪", " درصد")


def _dt(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = parse_iso(value)
    return value.astimezone(TEHRAN)


def jdate(value) -> str:
    t = _dt(value)
    return "—" if t is None else fa(jalali.jalali_str(t.date()))


def hm(value) -> str:
    t = _dt(value)
    return "—" if t is None else fa(t.strftime("%H:%M"))


def jdatetime(value) -> str:
    t = _dt(value)
    if t is None:
        return "—"
    return f"{fa(jalali.jalali_str(t.date()))} — {fa(t.strftime('%H:%M'))}"


def weekday(value) -> str:
    t = _dt(value)
    return "—" if t is None else jalali.weekday_name(t.date())


def jdate_long(value) -> str:
    t = _dt(value)
    if t is None:
        return "—"
    y, m, d = jalali.to_jalali(t.date())
    return f"{jalali.weekday_name(t.date())} {fa(d)} {jalali.month_name(m)} {fa(y)}"


def ago(seconds: float) -> str:
    s = int(max(0, seconds))
    if s < 90:
        return "همین حالا"
    m = s // 60
    if m < 60:
        return f"{fa(m)} دقیقه پیش"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{fa(h)} ساعت و {fa(m)} دقیقه پیش" if m else f"{fa(h)} ساعت پیش"
    return f"{fa(h // 24)} روز پیش"


def when(value, now) -> str:
    t, n = _dt(value), _dt(now)
    if t is None or n is None:
        return "—"
    days = (t.date() - n.date()).days
    day = {0: "امروز", 1: "فردا", -1: "دیروز"}.get(days) or f"{jalali.weekday_name(t.date())} {jdate(t)}"
    return f"ساعت {hm(t)} {day}"
