from __future__ import annotations

import configparser
import os
import re
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal, InvalidOperation
from pathlib import Path

from . import jalali

APP_ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER_TOKEN = "PUT_YOUR_TOKEN_HERE"
PROVIDERS = ("pricetoday", "alanchand")

WEEKDAY_KEYS = {
    "saturday": 5, "sunday": 6, "monday": 0, "tuesday": 1,
    "wednesday": 2, "thursday": 3, "friday": 4,
}

DEFAULTS: dict[str, dict[str, str]] = {
    "source": {
        "provider": "pricetoday",
        "mode": "live",
        "timeout_seconds": "20",
    },
    "pricetoday": {
        "url": "https://api.priceto.day/v1/latest/irr/usd",
        "unit": "rial",
        "sample_file": "tests/fixtures/pricetoday_usd_sample.json",
    },
    "alanchand": {
        "token": PLACEHOLDER_TOKEN,
        "url": "https://api.alanchand.com/",
        "type": "currencies",
        "slug": "usd",
        "field": "sell",
        "unit": "toman",
        "sample_file": "tests/fixtures/alanchand_currencies_sample.json",
        "source_utc_offset_minutes": "210",
    },
    "pricing": {
        "base_rate_toman": "260000",
        "base_date": "1405/07/20",
        "base_time": "09:00",
        "alpha_default": "1",
        "round_step_rial": "500000",
        "publish_threshold": "0.02",
        "approval_threshold": "0.05",
        "change_basis": "exact",
        "auto_publish": "false",
        "auto_renew": "true",
    },
    "guards": {
        "fetch_interval_minutes": "60",
        "retry_minutes": "10",
        "max_age_minutes": "180",
        "jump_threshold": "0.03",
        "min_rate_toman": "100000",
        "max_rate_toman": "2000000",
    },
    "hours": {
        "saturday": "09:00-17:00",
        "sunday": "09:00-17:00",
        "monday": "09:00-17:00",
        "tuesday": "09:00-17:00",
        "wednesday": "09:00-17:00",
        "thursday": "09:00-13:00",
        "friday": "closed",
        "holidays": "",
    },
    "web": {
        "host": "0.0.0.0",
        "port": "8080",
        "company_name": "برنا سنسور",
        "pricelist_title": "لیست قیمت",
        "validity_text": "تا پایان روز کاری",
        "show_rate_on_pricelist": "true",
        "footer_note": "",
        "session_hours": "12",
        "behind_https_proxy": "false",
    },
    "paths": {
        "database": "data/borna_pricing.sqlite3",
        "products_xlsx": "data/Borna_Step2-3_Products_Pricing.xlsx",
        "log_file": "data/borna_pricing.log",
        "secret_key_file": "data/secret.key",
        "evidence_dir": "data/evidence",
    },
    "market": {
        "own_sellers": "برنا سنسور",
        "min_price_usd": "2",
        "max_price_usd": "60",
        "jump_quarantine": "0.5",
        "stale_days": "14",
        "length_tolerance_mm": "30",
    },
    "collector": {
        "user_agent": "BornaMarketBot/1.2",
        "timeout_seconds": "20",
        "min_delay_seconds": "3",
        "max_bytes": "3000000",
        "max_upload_mb": "8",
    },
    "agents": {
        "enabled": "true",
        "poll_seconds": "60",
        "report_weekday": "saturday",
        "report_time": "10:00",
    },
}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class SourceCfg:
    provider: str
    mode: str
    timeout_seconds: int


@dataclass(frozen=True)
class PricetodayCfg:
    url: str
    unit: str
    sample_file: Path


@dataclass(frozen=True)
class AlanchandCfg:
    token: str
    url: str
    type: str
    slug: str
    field: str
    unit: str
    sample_file: Path
    source_utc_offset_minutes: int

    @property
    def token_is_set(self) -> bool:
        return bool(self.token) and self.token != PLACEHOLDER_TOKEN


@dataclass(frozen=True)
class PricingCfg:
    base_rate_toman: int
    base_date: date
    base_date_text: str
    base_time: time
    alpha_default: Decimal
    round_step_rial: int
    publish_threshold: Decimal
    approval_threshold: Decimal
    change_basis: str
    auto_publish: bool
    auto_renew: bool

    @property
    def base_rate_rial(self) -> int:
        return self.base_rate_toman * 10


@dataclass(frozen=True)
class GuardsCfg:
    fetch_interval_minutes: int
    retry_minutes: int
    max_age_minutes: int
    jump_threshold: Decimal
    min_rate_toman: int
    max_rate_toman: int


@dataclass(frozen=True)
class HoursCfg:
    days: dict
    holidays: frozenset


@dataclass(frozen=True)
class WebCfg:
    host: str
    port: int
    company_name: str
    pricelist_title: str
    validity_text: str
    show_rate_on_pricelist: bool
    footer_note: str
    session_hours: int
    behind_https_proxy: bool


@dataclass(frozen=True)
class PathsCfg:
    database: Path
    products_xlsx: Path
    log_file: Path
    secret_key_file: Path
    evidence_dir: Path


@dataclass(frozen=True)
class MarketCfg:
    own_sellers: tuple
    min_price_usd: Decimal
    max_price_usd: Decimal
    jump_quarantine: Decimal
    stale_days: int
    length_tolerance_mm: int


@dataclass(frozen=True)
class CollectorCfg:
    user_agent: str
    timeout_seconds: int
    min_delay_seconds: int
    max_bytes: int
    max_upload_mb: int


@dataclass(frozen=True)
class AgentsCfg:
    enabled: bool
    poll_seconds: int
    report_weekday: int
    report_time: time


@dataclass(frozen=True)
class Config:
    root: Path
    settings_path: Path
    settings_exists: bool
    source: SourceCfg
    pricetoday: PricetodayCfg
    alanchand: AlanchandCfg
    pricing: PricingCfg
    guards: GuardsCfg
    hours: HoursCfg
    web: WebCfg
    paths: PathsCfg
    market: MarketCfg
    collector: CollectorCfg
    agents: AgentsCfg


def _int(sec, key, raw, lo=None, hi=None) -> int:
    try:
        v = int(str(raw).replace(",", "").replace("_", "").strip())
    except ValueError:
        raise ConfigError(f"[{sec}] {key} باید عدد صحیح باشد، نه «{raw}».") from None
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ConfigError(f"[{sec}] {key} باید بین {lo} و {hi} باشد، نه {v}.")
    return v


def _dec(sec, key, raw, lo=None, hi=None) -> Decimal:
    try:
        v = Decimal(str(raw).strip())
    except InvalidOperation:
        raise ConfigError(f"[{sec}] {key} باید عدد باشد، نه «{raw}».") from None
    if not v.is_finite():
        raise ConfigError(f"[{sec}] {key} باید عدد باشد، نه «{raw}».")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ConfigError(f"[{sec}] {key} باید بین {lo} و {hi} باشد، نه {raw}.")
    return v


def _bool(sec, key, raw) -> bool:
    s = str(raw).strip().lower()
    if s in ("1", "true", "yes", "on", "بله"):
        return True
    if s in ("0", "false", "no", "off", "خیر"):
        return False
    raise ConfigError(f"[{sec}] {key} باید true یا false باشد، نه «{raw}».")


def _time(sec, key, raw) -> time:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(raw))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ConfigError(f"[{sec}] {key} باید ساعت به شکل 09:00 باشد، نه «{raw}».")
    return time(int(m.group(1)), int(m.group(2)))


def _day(key, raw):
    s = str(raw).strip().lower()
    if s in ("closed", "تعطیل", "off", ""):
        return None
    parts = s.split("-")
    if len(parts) != 2:
        raise ConfigError(f"[hours] {key} باید به شکل 09:00-17:00 یا closed باشد، نه «{raw}».")
    o, c = _time("hours", key, parts[0]), _time("hours", key, parts[1])
    if c <= o:
        raise ConfigError(f"[hours] {key}: ساعت پایان باید بعد از ساعت شروع باشد.")
    return (o, c)


def _path(root: Path, raw: str) -> Path:
    p = Path(str(raw).strip())
    return p if p.is_absolute() else (root / p)


def _choice(sec, key, raw, allowed) -> str:
    s = str(raw).strip().lower()
    if s not in allowed:
        raise ConfigError(f"[{sec}] {key} باید یکی از {' یا '.join(allowed)} باشد، نه «{raw}».")
    return s


def _url(sec, key, raw) -> str:
    s = str(raw).strip()
    if not s.lower().startswith(("http://", "https://")):
        raise ConfigError(f"[{sec}] {key} باید با http:// یا https:// شروع شود، نه «{raw}».")
    return s


def default_settings_path() -> Path:
    env = os.environ.get("BORNA_SETTINGS")
    return Path(env).resolve() if env else APP_ROOT / "settings.ini"


def load_config(path: str | Path | None = None) -> Config:
    settings_path = Path(path).resolve() if path else default_settings_path()
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=None)
    cp.read_dict(DEFAULTS)
    exists = settings_path.exists()
    if exists:
        try:
            with open(settings_path, encoding="utf-8-sig") as fh:
                cp.read_file(fh)
        except (configparser.Error, UnicodeDecodeError) as exc:
            raise ConfigError(f"فایل تنظیمات {settings_path} خوانا نیست (آن را با کدگذاری UTF-8 ذخیره کنید): {exc}") from None
    root = settings_path.parent

    s = cp["source"]
    source = SourceCfg(
        provider=_choice("source", "provider", s["provider"], PROVIDERS),
        mode=_choice("source", "mode", s["mode"], ("live", "file", "off")),
        timeout_seconds=_int("source", "timeout_seconds", s["timeout_seconds"], 1, 120),
    )

    t = cp["pricetoday"]
    pricetoday = PricetodayCfg(
        url=_url("pricetoday", "url", t["url"]),
        unit=_choice("pricetoday", "unit", t["unit"], ("rial", "toman")),
        sample_file=_path(root, t["sample_file"]),
    )

    a = cp["alanchand"]
    alan = AlanchandCfg(
        token=a["token"].strip(),
        url=_url("alanchand", "url", a["url"]),
        type=a["type"].strip(),
        slug=a["slug"].strip(),
        field=a["field"].strip(),
        unit=_choice("alanchand", "unit", a["unit"], ("toman", "rial")),
        sample_file=_path(root, a["sample_file"]),
        source_utc_offset_minutes=_int("alanchand", "source_utc_offset_minutes",
                                       a["source_utc_offset_minutes"], -720, 840),
    )

    p = cp["pricing"]
    basis = p["change_basis"].strip().lower()
    if basis not in ("exact", "rounded"):
        raise ConfigError("[pricing] change_basis باید exact یا rounded باشد.")
    try:
        base_date = jalali.parse_jalali(p["base_date"])
    except ValueError as exc:
        raise ConfigError(f"[pricing] base_date: {exc}") from None
    pricing = PricingCfg(
        base_rate_toman=_int("pricing", "base_rate_toman", p["base_rate_toman"], 1000, 100_000_000),
        base_date=base_date,
        base_date_text=jalali.jalali_str(base_date),
        base_time=_time("pricing", "base_time", p["base_time"]),
        alpha_default=_dec("pricing", "alpha_default", p["alpha_default"], Decimal(0), Decimal(1)),
        round_step_rial=_int("pricing", "round_step_rial", p["round_step_rial"], 1, 1_000_000_000),
        publish_threshold=_dec("pricing", "publish_threshold", p["publish_threshold"], Decimal(0), Decimal(1)),
        approval_threshold=_dec("pricing", "approval_threshold", p["approval_threshold"], Decimal(0), Decimal(1)),
        change_basis=basis,
        auto_publish=_bool("pricing", "auto_publish", p["auto_publish"]),
        auto_renew=_bool("pricing", "auto_renew", p["auto_renew"]),
    )

    g = cp["guards"]
    guards = GuardsCfg(
        fetch_interval_minutes=_int("guards", "fetch_interval_minutes", g["fetch_interval_minutes"], 1, 1440),
        retry_minutes=_int("guards", "retry_minutes", g["retry_minutes"], 1, 1440),
        max_age_minutes=_int("guards", "max_age_minutes", g["max_age_minutes"], 1, 100_000),
        jump_threshold=_dec("guards", "jump_threshold", g["jump_threshold"], Decimal(0), Decimal(1)),
        min_rate_toman=_int("guards", "min_rate_toman", g["min_rate_toman"], 1),
        max_rate_toman=_int("guards", "max_rate_toman", g["max_rate_toman"], 1),
    )
    if guards.min_rate_toman >= guards.max_rate_toman:
        raise ConfigError("[guards] min_rate_toman باید کمتر از max_rate_toman باشد.")

    h = cp["hours"]
    days = {wd: _day(k, h[k]) for k, wd in WEEKDAY_KEYS.items()}
    hol = set()
    for item in re.split(r"[,\s،]+", h["holidays"].strip()):
        if item:
            try:
                hol.add(jalali.parse_jalali(item))
            except ValueError as exc:
                raise ConfigError(f"[hours] holidays: {exc}") from None
    hours = HoursCfg(days=days, holidays=frozenset(hol))

    w = cp["web"]
    web = WebCfg(
        host=w["host"].strip(),
        port=_int("web", "port", w["port"], 1, 65535),
        company_name=w["company_name"].strip(),
        pricelist_title=w["pricelist_title"].strip(),
        validity_text=w["validity_text"].strip(),
        show_rate_on_pricelist=_bool("web", "show_rate_on_pricelist", w["show_rate_on_pricelist"]),
        footer_note=w["footer_note"].strip(),
        session_hours=_int("web", "session_hours", w["session_hours"], 1, 24 * 30),
        behind_https_proxy=_bool("web", "behind_https_proxy", w["behind_https_proxy"]),
    )

    pa = cp["paths"]
    paths = PathsCfg(
        database=_path(root, pa["database"]),
        products_xlsx=_path(root, pa["products_xlsx"]),
        log_file=_path(root, pa["log_file"]),
        secret_key_file=_path(root, pa["secret_key_file"]),
        evidence_dir=_path(root, pa["evidence_dir"]),
    )

    mk = cp["market"]
    market = MarketCfg(
        own_sellers=tuple(x.strip() for x in re.split(r"[,،\n]+", mk["own_sellers"]) if x.strip()),
        min_price_usd=_dec("market", "min_price_usd", mk["min_price_usd"], Decimal("0.01"), Decimal(100000)),
        max_price_usd=_dec("market", "max_price_usd", mk["max_price_usd"], Decimal("0.01"), Decimal(100000)),
        jump_quarantine=_dec("market", "jump_quarantine", mk["jump_quarantine"], Decimal("0.05"), Decimal(10)),
        stale_days=_int("market", "stale_days", mk["stale_days"], 1, 365),
        length_tolerance_mm=_int("market", "length_tolerance_mm", mk["length_tolerance_mm"], 0, 500),
    )
    if market.min_price_usd >= market.max_price_usd:
        raise ConfigError("[market] min_price_usd باید کمتر از max_price_usd باشد.")

    co = cp["collector"]
    ua = co["user_agent"].strip()
    if not ua or any(ch in ua for ch in "\r\n"):
        raise ConfigError("[collector] user_agent نباید خالی باشد.")
    collector = CollectorCfg(
        user_agent=ua,
        timeout_seconds=_int("collector", "timeout_seconds", co["timeout_seconds"], 1, 120),
        min_delay_seconds=_int("collector", "min_delay_seconds", co["min_delay_seconds"], 1, 600),
        max_bytes=_int("collector", "max_bytes", co["max_bytes"], 10_000, 50_000_000),
        max_upload_mb=_int("collector", "max_upload_mb", co["max_upload_mb"], 1, 9),
    )

    ag = cp["agents"]
    wd = ag["report_weekday"].strip().lower()
    if wd not in WEEKDAY_KEYS:
        raise ConfigError(f"[agents] report_weekday باید نام انگلیسی یک روز هفته باشد (مثل saturday)، نه «{wd}».")
    agents = AgentsCfg(
        enabled=_bool("agents", "enabled", ag["enabled"]),
        poll_seconds=_int("agents", "poll_seconds", ag["poll_seconds"], 5, 3600),
        report_weekday=WEEKDAY_KEYS[wd],
        report_time=_time("agents", "report_time", ag["report_time"]),
    )
    return Config(root=root, settings_path=settings_path, settings_exists=exists, source=source,
                  pricetoday=pricetoday, alanchand=alan, pricing=pricing, guards=guards, hours=hours, web=web,
                  paths=paths, market=market, collector=collector, agents=agents)
