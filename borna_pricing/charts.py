from __future__ import annotations

import json
import math
from datetime import datetime, timedelta

from markupsafe import Markup, escape

from . import fmt, jalali
from .clock import TEHRAN

SERIES = "#2a78d6"


def _nice_step(span: float, ticks: int = 4) -> float:
    raw = max(span, 1e-9) / ticks
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def _time_ticks(t0: datetime, t1: datetime) -> tuple[list[datetime], str]:
    span_h = (t1 - t0).total_seconds() / 3600
    a = t0.astimezone(TEHRAN)
    if span_h <= 36:
        step = next(h for h in (1, 2, 3, 4, 6, 12) if span_h / h <= 6)
        start = a.replace(minute=0, second=0, microsecond=0)
        if start < a:
            start += timedelta(hours=1)
        while start.hour % step:
            start += timedelta(hours=1)
        out, t = [], start
        while t <= t1:
            if t >= t0:
                out.append(t)
            t += timedelta(hours=step)
        return out, "hour"
    days = span_h / 24
    step = next(d for d in (1, 2, 3, 5, 7, 14, 30) if days / d <= 6)
    start = a.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    out, t = [], start
    while t <= t1:
        out.append(t)
        t += timedelta(days=step)
    return out, "day"


def _tick_label(t: datetime, kind: str) -> str:
    t = t.astimezone(TEHRAN)
    if kind == "hour":
        return fmt.fa(t.strftime("%H:%M"))
    y, m, d = jalali.to_jalali(t.date())
    return f"{fmt.fa(d)} {jalali.month_name(m)}"


def line_chart(points: list[tuple[datetime, float]], *, value_fmt, axis_fmt, unit: str,
               step: bool = False, extend_to: datetime | None = None,
               ref: tuple[float, str] | None = None, width: int = 920, height: int = 260) -> dict | None:
    if not points:
        return None
    pad_l, pad_r, pad_t, pad_b = 14, 96, 18, 34
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b
    xs = [p[0] for p in points]
    ys = [float(p[1]) for p in points]
    t0, t1 = xs[0], max(xs[-1], extend_to or xs[-1])
    if t1 <= t0:
        t0, t1 = t0 - timedelta(hours=12), t1 + timedelta(hours=12)
    lo, hi = min(ys), max(ys)
    if ref:
        lo, hi = min(lo, ref[0]), max(hi, ref[0])
    pad = max((hi - lo) * 0.12, abs(hi) * 0.01, 1)
    lo, hi = lo - pad, hi + pad
    st = _nice_step(hi - lo)
    lo, hi = math.floor(lo / st) * st, math.ceil(hi / st) * st
    span_t = (t1 - t0).total_seconds()

    def X(t):
        return pad_l + (t - t0).total_seconds() / span_t * pw

    def Y(v):
        return pad_t + (hi - v) / (hi - lo) * ph

    last_y = Y(ys[-1])
    o = [f'<svg viewBox="0 0 {width} {height}" class="chart-svg" role="img" direction="ltr" '
         f'aria-label="{escape(unit)}">']
    v = lo
    while v <= hi + st / 1000:
        y = Y(v)
        o.append(f'<line x1="{pad_l}" x2="{pad_l + pw}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        if abs(y - last_y) > 16:
            o.append(f'<text x="{pad_l + pw + 10}" y="{y + 4:.1f}" class="tick" text-anchor="start">'
                     f'{escape(axis_fmt(v))}</text>')
        v += st
    ticks, kind = _time_ticks(t0, t1)
    o.append(f'<line x1="{pad_l}" x2="{pad_l + pw}" y1="{pad_t + ph}" y2="{pad_t + ph}" class="axis"/>')
    for t in ticks:
        x = X(t)
        o.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{pad_t + ph}" y2="{pad_t + ph + 5}" class="axis"/>')
        o.append(f'<text x="{x:.1f}" y="{pad_t + ph + 20}" class="tick" text-anchor="middle">'
                 f'{escape(_tick_label(t, kind))}</text>')
    if ref:
        y = Y(ref[0])
        o.append(f'<line x1="{pad_l}" x2="{pad_l + pw}" y1="{y:.1f}" y2="{y:.1f}" class="ref"/>')
        o.append(f'<text x="{pad_l + 6}" y="{y - 6:.1f}" class="ref-label" text-anchor="start">'
                 f'{escape(ref[1])}</text>')
    pts = [(X(t), Y(v)) for t, v in zip(xs, ys)]
    if step:
        d = [f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            d.append(f"H{x1:.1f}V{y1:.1f}")
        end_x = X(t1)
        d.append(f"H{end_x:.1f}")
        last = (end_x, pts[-1][1])
    else:
        d = [f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"] + [f"L{x:.1f},{y:.1f}" for x, y in pts[1:]]
        last = pts[-1]
    path = " ".join(d)
    base_y = pad_t + ph
    o.append(f'<path d="{path} V{base_y:.1f} H{pts[0][0]:.1f} Z" class="area" style="fill:{SERIES}"/>')
    o.append(f'<path d="{path}" class="line" style="stroke:{SERIES}"/>')
    o.append(f'<circle cx="{last[0]:.1f}" cy="{last[1]:.1f}" r="5" class="end" style="fill:{SERIES}"/>')
    o.append(f'<g class="last"><rect x="{pad_l + pw + 4}" y="{last[1] - 11:.1f}" width="{pad_r - 8}" height="22" rx="4"/>'
             f'<text x="{pad_l + pw + 10}" y="{last[1] + 4:.1f}" text-anchor="start">{escape(axis_fmt(ys[-1]))}</text></g>')
    o.append(f'<line class="xhair" x1="0" x2="0" y1="{pad_t}" y2="{pad_t + ph}" visibility="hidden"/>')
    o.append(f'<circle class="xdot" r="5" cx="0" cy="0" visibility="hidden" style="fill:{SERIES}"/>')
    o.append(f'<rect class="hit" x="{pad_l}" y="{pad_t}" width="{pw}" height="{ph}"/>')
    o.append("</svg>")
    data = [{"x": round(x, 1), "y": round(y, 1), "v": f"{value_fmt(val)} {unit}", "t": fmt.jdatetime(t)}
            for (x, y), t, val in zip(pts, xs, ys)]
    return {"svg": Markup("".join(o)), "data": Markup(json.dumps({"w": width, "h": height, "pts": data},
                                                                  ensure_ascii=False).replace("</", "<\\/"))}


def axis_toman(v: float) -> str:
    return fmt.money(round(v))


def axis_rial_million(v: float) -> str:
    m = v / 1_000_000
    s = f"{m:.1f}".rstrip("0").rstrip(".")
    return fmt.fa(s.replace(".", "٫")) + " میلیون"
