from __future__ import annotations

from datetime import date

MONTHS = ("فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
          "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند")
WEEKDAYS = ("دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه")

_TO_LATIN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def g2j(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    g_d_m = (0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334)
    gy2 = gy + 1 if gm > 2 else gy
    days = (355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100
            + (gy2 + 399) // 400 + gd + g_d_m[gm - 1])
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


def j2g(jy: int, jm: int, jd: int) -> tuple[int, int, int]:
    jy += 1595
    days = (-355668 + 365 * jy + (jy // 33) * 8 + ((jy % 33) + 3) // 4 + jd
            + ((jm - 1) * 31 if jm < 7 else (jm - 7) * 30 + 186))
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0
    month_len = (0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    gm = 0
    while gm < 13 and gd > month_len[gm]:
        gd -= month_len[gm]
        gm += 1
    return gy, gm, gd


def to_jalali(d: date) -> tuple[int, int, int]:
    return g2j(d.year, d.month, d.day)


def from_jalali(jy: int, jm: int, jd: int) -> date:
    if not (1 <= jm <= 12 and 1 <= jd <= 31):
        raise ValueError(f"تاریخ شمسی نامعتبر: {jy}/{jm}/{jd}")
    g = date(*j2g(jy, jm, jd))
    if to_jalali(g) != (jy, jm, jd):
        raise ValueError(f"تاریخ شمسی نامعتبر: {jy}/{jm}/{jd}")
    return g


def parse_jalali(text: str) -> date:
    s = text.strip().translate(_TO_LATIN).replace("-", "/")
    parts = s.split("/")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"تاریخ شمسی باید به شکل 1405/07/20 باشد، نه «{text}».")
    return from_jalali(int(parts[0]), int(parts[1]), int(parts[2]))


def jalali_str(d: date) -> str:
    y, m, dd = to_jalali(d)
    return f"{y:04d}/{m:02d}/{dd:02d}"


def weekday_name(d: date) -> str:
    return WEEKDAYS[d.weekday()]


def month_name(m: int) -> str:
    return MONTHS[m - 1]
