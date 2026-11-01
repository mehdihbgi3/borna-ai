from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .textnorm import norm

PRICE_KINDS = {"price": "با قیمت", "quote_only": "بدون قیمت (استعلامی یا ناموجود)"}
AVAILABILITY_LABELS = {"in_stock": "موجود", "out_of_stock": "ناموجود", "unknown": "نامشخص"}
LEVEL_LABELS = {"retail": "خرده‌فروشی", "wholesale": "عمده", "unknown": "نامشخص"}
UNIT_LABELS = {"toman": "تومان", "rial": "ریال"}

_QUOTE = re.compile(r"تماس\s?بگیرید|تماس|استعلام|توافقی|call|inquiry")
_OOS = re.compile(r"ناموجود|موجود\s?نیست|اتمام\s?موجودی|تمام\s?شد|out\s?of\s?stock|outofstock|sold\s?out|soldout|discontinued")
_IN = re.compile(r"(?<!نا)موجود(?!\s?نیست)|in\s?stock|instock|available")
_TOMAN = re.compile(r"تومان|تومن|toman|\birt\b|(?<![\w])ت(?![\w])")
_RIAL = re.compile(r"ریال|rial|\birr\b")
_MILLION = re.compile(r"میلیون|million|\bm\b")
_THOUSAND = re.compile(r"هزار|thousand|\bk\b")
_GROUPED = re.compile(r"\d{1,3}(?:[,٬\s.]\d{3})+(?!\d)")
_DECIMAL = re.compile(r"\d+(?:[.٫/]\d+)?")


@dataclass(frozen=True)
class ParsedPrice:
    kind: str | None
    value_rial: int | None
    unit: str
    availability: str
    raw: str
    message: str = ""


def detect_unit(text: str, default: str) -> str:
    t = norm(text)
    if _RIAL.search(t):
        return "rial"
    if _TOMAN.search(t):
        return "toman"
    return default


def detect_availability(text: str | None) -> str:
    t = norm(text or "")
    if not t:
        return "unknown"
    if _OOS.search(t):
        return "out_of_stock"
    if _IN.search(t):
        return "in_stock"
    return "unknown"


def schema_availability(value) -> str:
    s = str(value or "").lower()
    if not s:
        return "unknown"
    if any(k in s for k in ("outofstock", "soldout", "discontinued")):
        return "out_of_stock"
    if any(k in s for k in ("instock", "limitedavailability", "onlineonly", "instoreonly", "preorder")):
        return "in_stock"
    return "unknown"


def _number(t: str, scaled: bool) -> Decimal | None:
    if not scaled:
        m = _GROUPED.search(t)
        if m:
            return Decimal(re.sub(r"[,٬\s.]", "", m.group(0)))
    m = _DECIMAL.search(t)
    if not m:
        return None
    try:
        return Decimal(m.group(0).replace("٫", ".").replace("/", "."))
    except InvalidOperation:
        return None


def parse_price(text, default_unit: str = "toman", availability: str | None = None) -> ParsedPrice:
    raw = str(text if text is not None else "").strip()
    t = norm(raw).replace("٬", ",")
    unit = detect_unit(t, default_unit)
    avail = availability or detect_availability(t)
    mult = Decimal(1_000_000) if _MILLION.search(t) else Decimal(1000) if _THOUSAND.search(t) else Decimal(1)
    num = _number(t, mult != 1)
    if num is None or num == 0:
        if _QUOTE.search(t) or num == 0 or avail == "out_of_stock":
            return ParsedPrice("quote_only", None, unit, avail, raw)
        return ParsedPrice(None, None, unit, avail, raw, "قیمتی در متن پیدا نشد.")
    value = num * mult
    if value != value.to_integral_value():
        return ParsedPrice(None, None, unit, avail, raw, "قیمت عدد صحیح نیست.")
    value = int(value)
    return ParsedPrice("price", value * 10 if unit == "toman" else value, unit, avail, raw)


def in_range(value_rial: int, min_toman: int, max_toman: int) -> bool:
    return min_toman * 10 <= value_rial <= max_toman * 10


def jump(new_rial: int, old_rial: int | None) -> Decimal | None:
    if not old_rial:
        return None
    return Decimal(new_rial) / Decimal(old_rial) - 1
