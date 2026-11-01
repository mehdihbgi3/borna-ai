from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta, timezone

UTC = timezone.utc
TEHRAN = timezone(timedelta(hours=3, minutes=30), "Asia/Tehran")

_fixed: datetime | None = None
_env_start = os.environ.get("BORNA_FAKE_NOW")
_env_anchor = time.monotonic()


def set_fixed_now(dt: datetime | None) -> None:
    global _fixed
    _fixed = None if dt is None else dt.astimezone(UTC).replace(microsecond=0)


def now_utc() -> datetime:
    if _fixed is not None:
        return _fixed
    if _env_start:
        start = parse_iso(_env_start)
        return (start + timedelta(seconds=time.monotonic() - _env_anchor)).replace(microsecond=0)
    return datetime.now(UTC).replace(microsecond=0)


def now_tehran() -> datetime:
    return now_utc().astimezone(TEHRAN)


def to_tehran(dt: datetime) -> datetime:
    return dt.astimezone(TEHRAN)


def iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return dt.astimezone(UTC).replace(microsecond=0).isoformat()


def parse_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def tehran_date(dt: datetime) -> date:
    return dt.astimezone(TEHRAN).date()


def tehran_day_start(dt: datetime) -> datetime:
    t = dt.astimezone(TEHRAN)
    return t.replace(hour=0, minute=0, second=0, microsecond=0)
