from __future__ import annotations

from . import textnorm as tn

SPEC_ATTRS = {
    "xref": "پارت‌نامبر معادل",
    "ecu": "ECU",
    "design": "طرح",
    "socket_color": "رنگ سوکت",
    "wire_color": "رنگ سیم",
    "length_mm": "طول سیم (میلی‌متر)",
    "wire_count": "تعداد سیم",
    "position": "موقعیت نصب",
    "vehicle": "خودروی سازگار",
}
MULTI_ATTRS = ("xref", "vehicle")
ATTR_ORDER = ("part_nos", "design", "ecu", "generation", "socket_color", "wire_color", "length_mm", "length_class",
              "wire_count", "position", "vehicles")


def spec_value(attr: str, raw: str) -> str:
    s = str(raw or "").strip()
    if not s:
        raise ValueError("مقدار خالی است.")
    if attr == "xref":
        pn = tn.normalize_pn(s)
        if not pn or len(pn) < 4:
            raise ValueError("پارت‌نامبر معتبر نیست.")
        return pn
    if attr == "ecu":
        ecus = tn.find_ecus(s)
        if len(ecus) != 1:
            raise ValueError("ECU باید یکی از زیمنس، ساژم، ME17، ME7 یا RE94 باشد.")
        return ecus[0]
    if attr == "design":
        d = tn.find_design(s)
        if not d:
            raise ValueError("طرح باید NTK، Bosch، Delphi یا Denso باشد.")
        return d
    if attr in ("socket_color", "wire_color"):
        c = tn.color_code(s)
        if not c:
            raise ValueError("رنگ شناخته نشد؛ مثلاً مشکی، طوسی، سبز، آبی، زرد یا سفید.")
        return c
    if attr == "length_mm":
        v = tn.latin_digits(s)
        if not v.isdigit() or not 50 <= int(v) <= 3000:
            raise ValueError("طول را به میلی‌متر و بین ۵۰ و ۳۰۰۰ وارد کنید.")
        return str(int(v))
    if attr == "wire_count":
        v = tn.latin_digits(s)
        if v not in ("1", "2", "3", "4", "5", "6"):
            raise ValueError("تعداد سیم باید عددی بین ۱ و ۶ باشد.")
        return v
    if attr == "position":
        pos = tn.find_position(s) or {"upstream": "upstream", "downstream": "downstream"}.get(s.lower())
        if not pos:
            raise ValueError("موقعیت باید «بالایی» یا «پایینی» باشد.")
        return pos
    if attr == "vehicle":
        found = tn.find_vehicles(s)
        if len(found) != 1:
            raise ValueError("نام خودرو شناخته نشد؛ یک خودرو از فهرست شناخته‌شده وارد کنید (مثل پراید، ۴۰۵، L90).")
        return found[0]
    raise ValueError("ویژگی نامعتبر است.")


def product_profile(p: dict, specs: list[dict] | None = None) -> dict:
    text = " ".join(x for x in (p.get("name"), p.get("catalog_name")) if x)
    parsed = tn.parse_title(text)
    prof: dict = {"key": p["key"], "family": "o2"}
    src: dict = {}

    def put(attr, value, origin):
        if value not in (None, "", []):
            prof[attr] = value
            src[attr] = origin

    pns = []
    for pn in [p.get("part_no_norm") or tn.normalize_pn(p.get("part_no"))] + parsed["part_nos"]:
        if pn and pn not in pns:
            pns.append(pn)
    put("part_nos", pns, "column")
    put("design", tn.find_design(p.get("design") or "") or parsed["design"],
        "column" if tn.find_design(p.get("design") or "") else "name")
    col_ecus = tn.find_ecus(p.get("ecu") or "")
    if len(col_ecus) == 1:
        put("ecu", col_ecus[0], "column")
    elif parsed["ecu"]:
        put("ecu", parsed["ecu"], "name")
    if tn.parse_title(p.get("ecu") or "")["generation"] or parsed["generation"]:
        put("generation", "new", "column")
    put("socket_color", tn.color_code(p.get("socket_color") or "") or parsed["socket_color"],
        "column" if p.get("socket_color") else "name")
    put("wire_color", tn.color_code(p.get("wire_color") or "") or parsed["wire_color"],
        "column" if p.get("wire_color") else "name")
    if p.get("length_mm"):
        put("length_mm", int(p["length_mm"]), "column")
    elif parsed["length_mm"]:
        put("length_mm", parsed["length_mm"], "name")
    lt_mm, lt_class = tn.find_length(p.get("length_text") or "")
    if lt_mm and "length_mm" not in prof:
        put("length_mm", lt_mm, "column")
    put("length_class", lt_class or parsed["length_class"], "column" if lt_class else "name")
    put("wire_count", parsed["wire_count"], "name")
    put("position", parsed["position"], "name")
    vehicles = []
    for v in tn.find_vehicles(p.get("vehicle") or "") + parsed["vehicles"]:
        if v not in vehicles:
            vehicles.append(v)
    put("vehicles", vehicles, "column" if p.get("vehicle") else "name")

    for s in specs or []:
        attr, value = s["attr"], s["value"]
        if attr == "xref":
            if value not in prof.setdefault("part_nos", []):
                prof["part_nos"].append(value)
            src["part_nos"] = "manual"
        elif attr == "vehicle":
            if value not in prof.setdefault("vehicles", []):
                prof["vehicles"].append(value)
            src["vehicles"] = "manual"
        elif attr in ("length_mm", "wire_count"):
            put(attr, int(value), "manual")
        else:
            put(attr, value, "manual")
    prof["sources"] = src
    return prof


def describe(prof: dict) -> list[tuple[str, str, str]]:
    rows = []
    labels = {"part_nos": "پارت‌نامبر", "design": "طرح", "ecu": "ECU", "generation": "نسل",
              "socket_color": "رنگ سوکت", "wire_color": "رنگ سیم", "length_mm": "طول سیم", "length_class": "رده طول",
              "wire_count": "تعداد سیم", "position": "موقعیت نصب", "vehicles": "خودرو", "tier": "اصالت",
              "brand": "برند"}
    origin = {"column": "ستون Excel", "name": "از نام کالا", "manual": "ثبت دستی", "listing": "از آگهی"}
    for attr in ATTR_ORDER + ("brand", "tier"):
        if attr not in prof or prof[attr] in (None, [], ""):
            continue
        rows.append((labels[attr], display(attr, prof[attr]), origin.get(prof.get("sources", {}).get(attr, ""), "")))
    return rows


def display(attr: str, value) -> str:
    if value in (None, "", []):
        return "—"
    if attr == "part_nos":
        return "، ".join(value)
    if attr == "design":
        return tn.DESIGN_LABELS.get(value, value)
    if attr in ("ecu",):
        return tn.ECU_LABELS.get(value, value)
    if attr == "ecus":
        return "، ".join(tn.ECU_LABELS.get(v, v) for v in value)
    if attr in ("socket_color", "wire_color"):
        return tn.COLOR_LABELS.get(value, value)
    if attr == "length_mm":
        return f"{value} میلی‌متر"
    if attr == "length_class":
        return tn.LENGTH_CLASS_LABELS.get(value, value)
    if attr == "position":
        return tn.POSITION_LABELS.get(value, value)
    if attr == "generation":
        return "نسل جدید" if value == "new" else value
    if attr == "vehicles":
        return "، ".join(tn.VEHICLE_LABELS.get(v, v) for v in value)
    if attr == "tier":
        return tn.TIER_LABELS.get(value, value)
    return str(value)
