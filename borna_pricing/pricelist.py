from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.page import PageMargins

from . import fmt, jalali
from .clock import TEHRAN, parse_iso

FONT = "Tahoma"
THIN = Side(style="thin", color="B8C2CC")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _al(**kw) -> Alignment:
    return Alignment(readingOrder=2, vertical="center", **kw)


def filename(pub: dict) -> str:
    t = parse_iso(pub["published_at"]).astimezone(TEHRAN)
    y, m, d = jalali.to_jalali(t.date())
    return f"Borna_PriceList_{y}{m:02d}{d:02d}_{t:%H%M}_no{pub['id']}.xlsx"


def build_xlsx(pub: dict, cfg) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "لیست قیمت"
    ws.sheet_view.rightToLeft = True
    ws.sheet_view.showGridLines = False
    cols = [("ردیف", 7), ("نام محصول", 52), ("پارت‌نامبر", 16), ("حداقل سفارش", 12),
            ("قیمت هر عدد (ریال)", 20), ("قیمت هر عدد (تومان)", 18)]
    for i, (_, w) in enumerate(cols, start=1):
        ws.column_dimensions[chr(64 + i)].width = w

    def line(r, text, size=10, bold=False, color="17202B"):
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=len(cols))
        c = ws.cell(row=r, column=1, value=text)
        c.font = Font(name=FONT, size=size, bold=bold, color=color)
        c.alignment = _al(horizontal="right")
        ws.row_dimensions[r].height = 22 if size <= 11 else 30

    pub_t = parse_iso(pub["published_at"])
    line(1, f"{cfg.web.company_name} — {cfg.web.pricelist_title}", size=15, bold=True)
    line(2, f"تاریخ: {fmt.jdate_long(pub_t)} — ساعت {fmt.hm(pub_t)}    (لیست شماره {fmt.fa(pub['id'])})")
    vu = pub.get("valid_until_eff") or parse_iso(pub["valid_until"])
    line(3, f"اعتبار: {cfg.web.validity_text} ({fmt.weekday(vu)} {fmt.jdate(vu)} ساعت {fmt.hm(vu)})")
    r = 4
    if cfg.web.show_rate_on_pricelist:
        line(r, f"مبنای نرخ دلار: {fmt.money(pub['rate_rial'] // 10)} تومان", color="4A5665")
        r += 1
    r += 1
    head_fill = PatternFill("solid", fgColor="17202B")
    for i, (name, _) in enumerate(cols, start=1):
        c = ws.cell(row=r, column=i, value=name)
        c.font = Font(name=FONT, size=10, bold=True, color="FFFFFF")
        c.fill = head_fill
        c.alignment = _al(horizontal="center", wrap_text=True)
        c.border = BORDER
    ws.row_dimensions[r].height = 30
    header_row = r
    zebra = PatternFill("solid", fgColor="F3F6F8")
    for k, row in enumerate(pub["rows"]):
        r += 1
        vals = [row["row_no"], row["name"], row["part_no"] or "", row["moq"], row["price_rial"],
                row["price_rial"] // 10]
        for i, v in enumerate(vals, start=1):
            c = ws.cell(row=r, column=i, value=v)
            c.font = Font(name=FONT, size=10, bold=(i == 5))
            c.border = BORDER
            if k % 2:
                c.fill = zebra
            if i in (5, 6):
                c.number_format = "#,##0"
                c.alignment = _al(horizontal="left")
            elif i == 2:
                c.alignment = _al(horizontal="right", wrap_text=True)
            else:
                c.alignment = _al(horizontal="center")
        ws.row_dimensions[r].height = 21
    r += 2
    if cfg.web.footer_note:
        line(r, cfg.web.footer_note, color="4A5665")
        r += 1
    line(r, "قیمت‌ها بر پایه نرخ روز دلار محاسبه شده و فقط در مدت اعتبار بالا معتبر است.", size=9, color="7D8895")
    ws.print_title_rows = f"{header_row}:{header_row}"
    ws.page_setup.orientation = "portrait"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins = PageMargins(left=0.4, right=0.4, top=0.5, bottom=0.5)
    ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
