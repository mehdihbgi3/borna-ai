from __future__ import annotations

from datetime import date, datetime, timedelta

from . import fmt
from .clock import TEHRAN

_ORDER = (5, 6, 0, 1, 2, 3, 4)


class BusinessHours:
    def __init__(self, hours_cfg):
        self.days = dict(hours_cfg.days)
        self.holidays = set(hours_cfg.holidays)

    def window(self, d: date) -> tuple[datetime, datetime] | None:
        if d in self.holidays:
            return None
        spec = self.days.get(d.weekday())
        if not spec:
            return None
        o, c = spec
        return (datetime.combine(d, o, TEHRAN), datetime.combine(d, c, TEHRAN))

    def is_open(self, dt: datetime) -> bool:
        t = dt.astimezone(TEHRAN)
        w = self.window(t.date())
        return bool(w) and w[0] <= t < w[1]

    def validity_end(self, dt: datetime) -> datetime:
        t = dt.astimezone(TEHRAN)
        for i in range(0, 60):
            d = t.date() + timedelta(days=i)
            w = self.window(d)
            if w and (i > 0 or t < w[1]):
                return w[1]
        return t + timedelta(days=1)

    def open_minutes_between(self, start: datetime, end: datetime) -> float:
        s, e = start.astimezone(TEHRAN), end.astimezone(TEHRAN)
        if e <= s:
            return 0.0
        if (e - s).days > 370:
            return (e - s).total_seconds() / 60
        total = 0.0
        d = s.date()
        while d <= e.date():
            w = self.window(d)
            if w:
                a, b = max(w[0], s), min(w[1], e)
                if b > a:
                    total += (b - a).total_seconds() / 60
            d += timedelta(days=1)
        return total

    def close_today(self, dt: datetime) -> datetime | None:
        w = self.window(dt.astimezone(TEHRAN).date())
        return w[1] if w else None

    def next_open(self, dt: datetime) -> datetime | None:
        t = dt.astimezone(TEHRAN)
        if self.is_open(t):
            return t
        for i in range(0, 60):
            d = t.date() + timedelta(days=i)
            w = self.window(d)
            if w and t < w[0]:
                return w[0]
            if w and i == 0 and w[0] <= t < w[1]:
                return t
        return None

    def describe(self) -> list[tuple[str, str]]:
        from .jalali import WEEKDAYS
        out = []
        for wd in _ORDER:
            spec = self.days.get(wd)
            if spec:
                out.append((WEEKDAYS[wd], f"{fmt.fa(spec[0].strftime('%H:%M'))} تا {fmt.fa(spec[1].strftime('%H:%M'))}"))
            else:
                out.append((WEEKDAYS[wd], "تعطیل"))
        return out
