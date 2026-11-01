from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from openpyxl import load_workbook

_LATIN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

FIELDS = [
    ("key", ("کد یکتا", "کد کالا", "کد")),
    ("row_no", ("ردیف",)),
    ("status", ("وضعیت",)),
    ("name", ("نام در لیست قیمت", "نام محصول", "نام کالا")),
    ("catalog_name", ("نام در کاتالوگ",)),
    ("part_no", ("پارت نامبر بین المللی", "پارت نامبر", "شماره فنی")),
    ("design", ("طرح",)),
    ("ecu", ("سیستم انژکتور", "ecu")),
    ("vehicle", ("خودرو",)),
    ("length_text", ("طول سیم در نام", "طول سیم")),
    ("socket_color", ("رنگ سوکت",)),
    ("wire_color", ("رنگ سیم",)),
    ("base_price_rial", ("قیمت پایه",)),
    ("moq", ("حداقل سفارش", "حداقل تعداد")),
    ("alpha", ("α اختصاصی", "ضریب ارزی", "alpha")),
    ("note", ("پرسش یا توضیح", "توضیح")),
]
REQUIRED = ("key", "name", "base_price_rial")
TEXT_FIELDS = ("status", "name", "catalog_name", "part_no", "design", "ecu", "vehicle", "length_text",
               "socket_color", "wire_color", "note")
SKIP_HEADER_WORDS = ("خودکار",)


class ImportErrorFa(Exception):
    pass


def _norm(text) -> str:
    s = str(text or "").replace("‌", " ").replace("ي", "ی").replace("ك", "ک")
    return re.sub(r"\s+", " ", s).strip().lower()


def _int(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError
    if isinstance(value, (int, float)):
        if float(value) != int(value):
            raise ValueError
        return int(value)
    s = str(value).translate(_LATIN).replace(",", "").replace("٬", "").strip()
    return int(Decimal(s))


def normalize_part_no(pn: str | None) -> str | None:
    if not pn:
        return None
    pn = str(pn).strip()
    if len(pn) == 9 and pn.startswith("258") and pn.isdigit():
        return "0" + pn
    return pn.upper()


def length_from_key(key: str) -> int | None:
    m = re.fullmatch(r"FIB\d+-(\d+)", key.strip(), flags=re.I)
    return int(m.group(1)) if m else None


def _find_header(ws):
    for r in range(1, 21):
        for c in range(1, min(ws.max_column, 60) + 1):
            if _norm(ws.cell(row=r, column=c).value).startswith(("کد یکتا", "کد کالا")):
                return r
    raise ImportErrorFa("سطر عنوان پیدا نشد: ستونی با عنوان «کد یکتا در سامانه» یا «کد کالا» لازم است.")


def read_products(path: str | Path, round_step_rial: int = 500000):
    try:
        wb = load_workbook(path, data_only=True, read_only=False)
    except Exception as exc:
        raise ImportErrorFa(f"فایل Excel باز نشد: {exc}") from None
    ws = wb["محصولات"] if "محصولات" in wb.sheetnames else wb.worksheets[0]
    hdr = _find_header(ws)
    headers = {c: _norm(ws.cell(row=hdr, column=c).value) for c in range(1, ws.max_column + 1)}
    colmap: dict[str, int] = {}
    for field, prefixes in FIELDS:
        for pref in prefixes:
            pref_n = _norm(pref)
            hit = next((c for c, h in headers.items()
                        if h.startswith(pref_n) and c not in colmap.values()
                        and not any(w in h for w in SKIP_HEADER_WORDS)), None)
            if hit:
                colmap[field] = hit
                break
    missing = [f for f in REQUIRED if f not in colmap]
    if missing:
        raise ImportErrorFa("ستون‌های لازم پیدا نشد: " + "، ".join(missing))

    products, errors, warnings = [], [], []
    seen: dict[str, int] = {}
    for r in range(hdr + 1, ws.max_row + 1):
        raw = {f: ws.cell(row=r, column=c).value for f, c in colmap.items()}
        if all(v in (None, "") for v in raw.values()):
            continue
        where = f"سطر {r}"
        key = str(raw.get("key") or "").strip()
        if not key:
            errors.append(f"{where}: کد کالا خالی است.")
            continue
        if key in seen:
            errors.append(f"{where}: کد «{key}» تکراری است (سطر {seen[key]}).")
            continue
        seen[key] = r
        item = {"key": key}
        for f in TEXT_FIELDS:
            v = raw.get(f)
            item[f] = None if v in (None, "") else str(v).strip()
        if not item["name"]:
            errors.append(f"{where} ({key}): نام کالا خالی است.")
            continue
        try:
            price = _int(raw.get("base_price_rial"))
            if price is None or price <= 0:
                raise ValueError
        except (ValueError, InvalidOperation):
            errors.append(f"{where} ({key}): قیمت پایه باید عدد صحیح مثبت به ریال باشد، نه «{raw.get('base_price_rial')}».")
            continue
        item["base_price_rial"] = price
        if price % round_step_rial:
            warnings.append(f"{key}: قیمت پایه {price:,} مضرب گام گرد کردن نیست؛ اولین محاسبه آن را گرد می‌کند.")
        try:
            item["row_no"] = _int(raw.get("row_no")) or len(products) + 1
            item["moq"] = _int(raw.get("moq"))
        except (ValueError, InvalidOperation):
            errors.append(f"{where} ({key}): ردیف یا حداقل سفارش عدد صحیح نیست.")
            continue
        a = raw.get("alpha")
        if a in (None, ""):
            item["alpha"] = None
        else:
            try:
                ad = Decimal(str(a).translate(_LATIN).strip())
                if not (0 <= ad <= 1):
                    raise InvalidOperation
                item["alpha"] = str(ad.normalize())
            except InvalidOperation:
                errors.append(f"{where} ({key}): α باید عددی بین ۰ و ۱ باشد، نه «{a}».")
                continue
        item["part_no_norm"] = normalize_part_no(item["part_no"])
        item["length_mm"] = length_from_key(key)
        products.append(item)
    if not products and not errors:
        errors.append("هیچ کالایی در فایل پیدا نشد.")
    return products, errors, warnings
