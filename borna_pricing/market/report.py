from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from io import BytesIO

from .. import fmt
from ..clock import iso
from .prices import LEVEL_LABELS
from .textnorm import TIER_LABELS

NEAR = Decimal("0.05")


def _chg(new, old):
    if not new or not old:
        return None
    return Decimal(new) / Decimal(old) - 1


def _rate_toman(c, at: str):
    row = c.execute("SELECT value_toman FROM rates WHERE accepted=1 AND fetched_at<=? ORDER BY fetched_at DESC, id DESC"
                    " LIMIT 1", (at,)).fetchone()
    return row["value_toman"] if row else None


def build(ms, end: datetime) -> dict:
    start = end - timedelta(days=7)
    with ms.db() as c:
        off = ms._official(c)
        products = [dict(p) for p in c.execute("SELECT * FROM products WHERE active=1 ORDER BY row_no, key")]
        now_rows = [r for r in ms.competitor_rows(c, end) if r["fresh"] and not r["is_own"]]
        old_rows = [r for r in ms.competitor_rows(c, start) if r["fresh"] and not r["is_own"]]
        fx_end, fx_start = _rate_toman(c, iso(end)), _rate_toman(c, iso(start))
        new_listings = c.execute("SELECT COUNT(*) FROM external_products WHERE first_seen_at>? AND first_seen_at<=?",
                                 (iso(start), iso(end))).fetchone()[0]
        to_review = c.execute("SELECT COUNT(*) FROM external_products WHERE match_state='proposed'").fetchone()[0]
        obs_review = c.execute("SELECT COUNT(*) FROM observations WHERE status IN ('pending','quarantined')").fetchone()[0]
        failing = [dict(r) for r in c.execute(
            "SELECT name, failure_count, last_error FROM sources WHERE failure_count>0 AND active=1 ORDER BY name")]
    fx_chg = _chg(fx_end, fx_start)
    rows, insights = [], []
    for p in products:
        eq_now = [r for r in now_rows if r["product_key"] == p["key"] and r["relation"] == "equivalent"]
        eq_old = [r for r in old_rows if r["product_key"] == p["key"] and r["relation"] == "equivalent"]
        sub_now = [r for r in now_rows if r["product_key"] == p["key"] and r["relation"] == "substitute"]
        st, st_old, st_sub = ms.stats(eq_now), ms.stats(eq_old), ms.stats(sub_now)
        official = off["prices"].get(p["key"])
        level = next((lv for lv in ("wholesale", "retail", "unknown") if st[lv]["median"]), None)
        med = st[level]["median"] if level else None
        med_old = st_old[level]["median"] if level else None
        position = _chg(official, med)
        comp_chg = _chg(med, med_old)
        priced = [r for r in eq_now + sub_now if r["obs"]["kind"] == "price" and r["obs"]["availability"] != "out_of_stock"]
        cheapest = min(priced, key=lambda r: r["obs"]["price_rial"], default=None)
        oos = sum(1 for r in eq_now + sub_now if r["obs"]["availability"] == "out_of_stock")
        row = {"key": p["key"], "name": p["name"], "official": official, "level": level, "median": med,
               "median_old": med_old, "position": str(position) if position is not None else None,
               "competitor_change": str(comp_chg) if comp_chg is not None else None,
               "n": len(eq_now), "n_sub": len(sub_now), "oos": oos,
               "wholesale_median": st["wholesale"]["median"], "retail_median": st["retail"]["median"],
               "substitute_median": st_sub["wholesale"]["median"] or st_sub["retail"]["median"] or st_sub["unknown"]["median"],
               "cheapest": ({"seller": cheapest["seller"] or cheapest["source_name"], "price": cheapest["obs"]["price_rial"],
                             "level": cheapest["obs"]["price_level"], "tier": cheapest["tier"],
                             "relation": cheapest["relation"]} if cheapest else None)}
        rows.append(row)
        if position is not None and abs(position) > NEAR:
            side = "ارزان‌تر" if position < 0 else "گران‌تر"
            where = LEVEL_LABELS[level]
            insights.append({"kind": "position", "key": p["key"],
                             "text": f"{p['name']} ({p['key']}): قیمت رسمی برنا {fmt.pct_plain_words(abs(position))} "
                                     f"{side} از میانه قیمت {where} رقبای هم‌طرح است "
                                     f"({fmt.fa(st[level]['n_priced'])} قیمت)."
                                     + (" قیمت برنا عمده است و رقبا خرده‌فروشی؛ فاصله شامل سود فروشنده است."
                                        if level == "retail" else "")})
        if fx_chg is not None and comp_chg is not None and abs(fx_chg) >= Decimal("0.02"):
            if abs(comp_chg) < abs(fx_chg) / 2:
                insights.append({"kind": "lag", "key": p["key"],
                                 "text": f"{p['name']}: دلار این هفته {fmt.pct_words(fx_chg)} شد ولی میانه قیمت رقبا "
                                         f"{fmt.pct_words(comp_chg)}؛ رقبا هنوز با دلار هم‌قدم نشده‌اند."})
        if oos:
            insights.append({"kind": "oos", "key": p["key"],
                             "text": f"{p['name']}: {fmt.fa(oos)} آگهی رقیب ناموجود است؛ فرصت پیشنهاد به مشتریان آن‌ها."})
    if new_listings:
        insights.append({"kind": "new", "key": None, "text": f"{fmt.fa(new_listings)} آگهی تازه در این هفته پیدا شد."})
    if to_review or obs_review:
        insights.append({"kind": "review", "key": None,
                         "text": f"{fmt.fa(to_review)} آگهی منتظر تأیید تطبیق و {fmt.fa(obs_review)} قیمت منتظر بررسی است."})
    for s in failing:
        insights.append({"kind": "source", "key": None,
                         "text": f"منبع «{s['name']}» {fmt.fa(s['failure_count'])} بار پشت سر هم خوانده نشد: "
                                 f"{(s['last_error'] or '')[:160]}"})
    return {"period_start": iso(start), "period_end": iso(end), "fx_start": fx_start, "fx_end": fx_end,
            "fx_change": str(fx_chg) if fx_chg is not None else None, "rows": rows, "insights": insights,
            "new_listings": new_listings, "to_review": to_review, "obs_review": obs_review,
            "publication_id": off["publication"]["id"] if off["publication"] else None}


def xlsx(rep: dict, company: str) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    data = rep["data"]
    wb = Workbook()
    ws = wb.active
    ws.title = "خلاصه"
    ws.sheet_view.rightToLeft = True
    bold = Font(bold=True)
    head = PatternFill("solid", fgColor="17202B")
    ws["A1"] = f"گزارش هفتگی بازار — {company}"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"از {fmt.jdatetime(data['period_start'])} تا {fmt.jdatetime(data['period_end'])}"
    if data.get("fx_end"):
        ws["A3"] = (f"دلار آزاد: {fmt.money(data['fx_start'])} ← {fmt.money(data['fx_end'])} تومان"
                    + (f" ({fmt.pct(Decimal(data['fx_change']))})" if data.get("fx_change") else ""))
    ws["A5"] = "نکته‌ها"
    ws["A5"].font = bold
    for i, ins in enumerate(data["insights"], start=6):
        ws.cell(row=i, column=1, value=ins["text"]).alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 120

    t = wb.create_sheet("کالاها")
    t.sheet_view.rightToLeft = True
    cols = ["کد", "نام کالا", "قیمت رسمی برنا (ریال)", "میانه رقبای هم‌طرح (ریال)", "سطح قیمت مقایسه",
            "فاصله برنا با میانه", "تغییر میانه رقبا در هفته", "میانه عمده", "میانه خرده", "میانه جایگزین‌ها",
            "تعداد آگهی هم‌طرح", "تعداد آگهی جایگزین", "ناموجود", "ارزان‌ترین فروشنده", "قیمت ارزان‌ترین (ریال)",
            "اصالت ارزان‌ترین"]
    for j, name in enumerate(cols, start=1):
        cell = t.cell(row=1, column=j, value=name)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for i, r in enumerate(data["rows"], start=2):
        ch = r.get("cheapest") or {}
        vals = [r["key"], r["name"], r["official"], r["median"], LEVEL_LABELS.get(r["level"], "—") if r["level"] else "—",
                float(Decimal(r["position"])) if r["position"] else None,
                float(Decimal(r["competitor_change"])) if r["competitor_change"] else None,
                r["wholesale_median"], r["retail_median"], r["substitute_median"], r["n"], r["n_sub"], r["oos"],
                ch.get("seller"), ch.get("price"), TIER_LABELS.get(ch.get("tier"), "") if ch else None]
        for j, v in enumerate(vals, start=1):
            cell = t.cell(row=i, column=j, value=v)
            if j in (3, 4, 8, 9, 10, 15):
                cell.number_format = "#,##0"
            if j in (6, 7):
                cell.number_format = "+0.0%;-0.0%;0.0%"
    for j, w in enumerate([13, 46, 16, 16, 12, 12, 12, 14, 14, 14, 10, 10, 9, 26, 16, 14], start=1):
        t.column_dimensions[t.cell(row=1, column=j).column_letter].width = w
    t.freeze_panes = "C2"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
