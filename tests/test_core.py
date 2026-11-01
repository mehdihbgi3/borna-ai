import json
from datetime import date, datetime, time
from decimal import Decimal

import pytest

from borna_pricing import config, engine, fmt, jalali
from borna_pricing.clock import TEHRAN
from borna_pricing.hours import BusinessHours

from conftest import FIXTURES, write_settings


@pytest.mark.parametrize("g, j", [
    (date(2026, 10, 31), (1405, 8, 9)),
    (date(2026, 10, 12), (1405, 7, 20)),
    (date(2026, 3, 21), (1405, 1, 1)),
    (date(2025, 3, 21), (1404, 1, 1)),
    (date(2025, 3, 20), (1403, 12, 30)),
    (date(2024, 3, 20), (1403, 1, 1)),
    (date(2027, 3, 20), (1405, 12, 29)),
    (date(2027, 3, 21), (1406, 1, 1)),
])
def test_jalali_known_dates(g, j):
    assert jalali.to_jalali(g) == j
    assert jalali.from_jalali(*j) == g


def test_jalali_round_trip_ten_years():
    d = date(2020, 1, 1)
    while d < date(2032, 1, 1):
        assert jalali.from_jalali(*jalali.to_jalali(d)) == d
        d = date.fromordinal(d.toordinal() + 1)


def test_jalali_parse_and_reject():
    assert jalali.parse_jalali("۱۴۰۵/۰۷/۲۰") == date(2026, 10, 12)
    assert jalali.parse_jalali("1405-07-20") == date(2026, 10, 12)
    for bad in ("1405/07/31", "1405/12/30", "1405/13/01", "20/07/1405", "abc"):
        with pytest.raises(ValueError):
            jalali.parse_jalali(bad)
    assert jalali.weekday_name(date(2026, 10, 31)) == "شنبه"


def test_persian_formatting():
    assert fmt.money(23000000) == "۲۳٬۰۰۰٬۰۰۰"
    assert fmt.pct(Decimal("0.051538")) == "+۵٫۱۵٪"
    assert fmt.pct(Decimal("-0.0213")) == "−۲٫۱۳٪"
    assert fmt.pct(0) == "۰٫۰۰٪"
    assert fmt.pct(None) == "—"
    dt = datetime(2026, 10, 31, 13, 20, tzinfo=TEHRAN)
    assert fmt.jdatetime(dt) == "۱۴۰۵/۰۸/۰۹ — ۱۳:۲۰"
    assert fmt.jdate_long(dt) == "شنبه ۹ آبان ۱۴۰۵"


def test_defaults_match_step1_decisions(tmp_path):
    cfg = config.load_config(write_settings(tmp_path))
    p = cfg.pricing
    assert p.base_rate_toman == 260000 and p.base_rate_rial == 2600000
    assert p.alpha_default == 1 and p.round_step_rial == 500000
    assert p.publish_threshold == Decimal("0.02") and p.approval_threshold == Decimal("0.05")
    assert p.base_date == date(2026, 10, 12) and p.auto_publish is False
    assert cfg.alanchand.slug == "usd" and cfg.alanchand.field == "sell" and cfg.alanchand.unit == "toman"
    assert cfg.alanchand.token_is_set is False
    assert cfg.hours.days[4] is None
    assert cfg.hours.days[3] == (time(9), time(13))
    assert cfg.paths.database == tmp_path / "data" / "test.sqlite3"


def test_source_defaults_to_pricetoday_even_for_old_settings_files(tmp_path):
    old = tmp_path / "settings.ini"
    old.write_text("[alanchand]\ntoken = OLD-TOKEN-1234\nurl = https://api.alanchand.com/\ntype = currencies\n"
                   "slug = usd\nfield = sell\nunit = toman\nmode = live\n"
                   "sample_file = tests/fixtures/alanchand_currencies_sample.json\ntimeout_seconds = 20\n"
                   "source_utc_offset_minutes = 210\n\n[guards]\nfetch_interval_minutes = 60\n", encoding="utf-8")
    cfg = config.load_config(old)
    assert cfg.source.provider == "pricetoday" and cfg.source.mode == "live" and cfg.source.timeout_seconds == 20
    assert cfg.pricetoday.url == "https://api.priceto.day/v1/latest/irr/usd" and cfg.pricetoday.unit == "rial"
    assert cfg.alanchand.token == "OLD-TOKEN-1234" and cfg.alanchand.token_is_set
    example = config.load_config(config.APP_ROOT / "settings.example.ini")
    assert example.source.provider == "pricetoday" and example.pricetoday.sample_file.exists()


@pytest.mark.parametrize("old, new, word", [
    ("provider = alanchand", "provider = bonbast", "provider"),
    ("provider = alanchand", "provider = alanchand\ntimeout_seconds = 0", "timeout_seconds"),
    ("[pricetoday]", "[pricetoday]\nunit = dollar", "unit"),
    ("[pricetoday]", "[pricetoday]\nurl = api.priceto.day/v1", "url"),
])
def test_invalid_source_settings_are_reported(tmp_path, old, new, word):
    path = write_settings(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")
    with pytest.raises(config.ConfigError) as e:
        config.load_config(path)
    assert word in str(e.value)


@pytest.mark.parametrize("extra, word", [
    ("[pricing]\nround_step_rial = abc", "round_step_rial"),
    ("[pricing]\nalpha_default = 1.5", "alpha_default"),
    ("[hours]\nfriday = 17:00-09:00", "friday"),
    ("[pricing]\nbase_date = 1405/07/31", "base_date"),
    ("MODE", "mode"),
    ("[alanchand]\nslug = usd", "خوانا نیست"),
])
def test_invalid_settings_are_reported_in_persian(tmp_path, extra, word):
    path = write_settings(tmp_path, "" if extra == "MODE" else extra)
    if extra == "MODE":
        path.write_text(path.read_text(encoding="utf-8").replace("mode = file", "mode = maybe"), encoding="utf-8")
    with pytest.raises(config.ConfigError) as e:
        config.load_config(path)
    assert word in str(e.value)


def test_business_hours_and_validity(tmp_path):
    cfg = config.load_config(write_settings(tmp_path, "[hours]\nholidays = 1405/08/10"))
    bh = BusinessHours(cfg.hours)
    sat = datetime(2026, 10, 31, 13, 33, tzinfo=TEHRAN)
    assert bh.is_open(sat)
    assert bh.validity_end(sat) == datetime(2026, 10, 31, 17, 0, tzinfo=TEHRAN)
    late = datetime(2026, 10, 31, 18, 0, tzinfo=TEHRAN)
    assert not bh.is_open(late)
    assert bh.validity_end(late) == datetime(2026, 11, 2, 17, 0, tzinfo=TEHRAN)
    assert bh.next_open(late) == datetime(2026, 11, 2, 9, 0, tzinfo=TEHRAN)
    thu = datetime(2026, 11, 5, 12, 0, tzinfo=TEHRAN)
    assert bh.validity_end(thu) == datetime(2026, 11, 5, 13, 0, tzinfo=TEHRAN)
    fri = datetime(2026, 11, 6, 10, 0, tzinfo=TEHRAN)
    assert not bh.is_open(fri)
    assert bh.validity_end(fri) == datetime(2026, 11, 7, 17, 0, tzinfo=TEHRAN)


def test_open_minutes_count_only_business_hours(tmp_path):
    bh = BusinessHours(config.load_config(write_settings(tmp_path, "[hours]\nholidays = 1405/08/10")).hours)
    t = lambda *a: datetime(*a, tzinfo=TEHRAN)
    assert bh.open_minutes_between(t(2026, 10, 31, 16, 0), t(2026, 11, 2, 10, 0)) == 120
    assert bh.open_minutes_between(t(2026, 11, 5, 12, 50), t(2026, 11, 7, 9, 20)) == 30
    assert bh.open_minutes_between(t(2026, 11, 2, 19, 59), t(2026, 11, 3, 8, 59)) == 0
    assert bh.open_minutes_between(t(2026, 11, 3, 10, 0), t(2026, 11, 3, 9, 0)) == 0
    assert bh.open_minutes_between(t(2020, 1, 1, 9, 0), t(2026, 11, 3, 9, 0)) > 3_000_000


P = engine.PricingParams(base_rate_rial=2600000, alpha_default=Decimal(1), round_step_rial=500000,
                         publish_threshold=Decimal("0.02"), approval_threshold=Decimal("0.05"))


def test_formula_matches_excel_prototype_for_all_products():
    data = json.loads((FIXTURES / "excel_expected.json").read_text(encoding="utf-8"))
    checked = 0
    for sc in data["scenarios"]:
        rate_rial = sc["rate_toman"] * 10
        for key, exp in sc["expected"].items():
            alpha = Decimal(str(sc["alpha_overrides"].get(key, 1)))
            ex = engine.exact_price(exp["base"], alpha, rate_rial, 2600000)
            assert float(ex) == pytest.approx(exp["exact"], abs=0.01), (sc["file"], key)
            assert engine.round_price(ex, 500000) == exp["rounded"], (sc["file"], key)
            checked += 1
    assert checked == 4 * 27


def test_rounding_is_half_up_like_excel():
    assert engine.round_price(Decimal(22750000), 500000) == 23000000
    assert engine.round_price(Decimal("22749999.99"), 500000) == 22500000
    assert engine.round_price(Decimal(250000), 500000) == 500000


def _base_list():
    prods = [engine.ProductInput("A", 22000000), engine.ProductInput("B", 21500000),
             engine.ProductInput("EF7", 34500000)]
    base = engine.RefList(2600000, {p.key: p.base_price_rial for p in prods})
    return prods, base


def test_todays_rate_against_base_list():
    prods, base = _base_list()
    res = engine.compute_run(prods, 2734000, P, base, base)
    prices = {r.key: r.price_rial for r in res.rows}
    assert prices == {"A": 23000000, "B": 22500000, "EF7": 36500000}
    assert res.needs_publish and res.needs_approval
    assert all(r.flag_approval for r in res.rows)
    assert res.rows[0].fx_effect_vs_pub.quantize(Decimal("0.0001")) == Decimal("0.0515")


def test_rounded_basis_reproduces_excel_flags():
    prods, base = _base_list()
    params = engine.PricingParams(**{**P.__dict__, "change_basis": "rounded"})
    res = engine.compute_run(prods, 2734000, params, base, base)
    assert [r.key for r in res.rows if r.flag_approval] == ["EF7"]
    assert res.needs_publish and res.needs_approval


def test_small_wiggle_near_rounding_boundary_does_not_trigger_publication():
    prods, base = _base_list()
    first = engine.compute_run(prods, 2740000, P, base, base)
    pub = engine.RefList(2740000, {r.key: r.price_rial for r in first.rows})
    wiggle = engine.compute_run(prods, 2748000, P, pub, pub)
    a = wiggle.rows[0]
    assert (a.pub_price_rial, a.price_rial) == (23000000, 23500000)
    assert a.change_vs_pub > Decimal("0.02")
    assert not wiggle.needs_publish
    rounded = engine.PricingParams(**{**P.__dict__, "change_basis": "rounded"})
    assert engine.compute_run(prods, 2748000, rounded, pub, pub).needs_publish


def test_publish_threshold_two_percent_and_daily_cap():
    prods, base = _base_list()
    pub = engine.RefList(2600000, base.prices)
    assert not engine.compute_run(prods, 2652000, P, pub, pub).needs_publish
    r = engine.compute_run(prods, 2652260, P, pub, pub)
    assert r.needs_publish and not r.needs_approval
    r = engine.compute_run(prods, 2470000, P, pub, pub)
    assert r.needs_publish and not r.needs_approval
    r = engine.compute_run(prods, 2469000, P, pub, pub)
    assert r.needs_approval


def test_new_changed_and_removed_products_force_publication():
    prods, base = _base_list()
    pub = engine.RefList(2600000, base.prices)
    changed = [engine.ProductInput("A", 23000000)] + prods[1:]
    r = engine.compute_run(changed, 2600000, P, pub, pub)
    assert r.needs_publish and [x.key for x in r.rows if x.flag_publish] == ["A"]
    added = prods + [engine.ProductInput("NEW", 20000000)]
    r = engine.compute_run(added, 2600000, P, pub, pub)
    assert r.needs_publish and [x.key for x in r.rows if x.flag_publish] == ["NEW"]
    r = engine.compute_run(prods[:2], 2600000, P, pub, pub)
    assert r.needs_publish and r.removed_keys == ["EF7"]
    assert not engine.compute_run(prods, 2600000, P, pub, pub).needs_publish


def test_alpha_override():
    ex = engine.exact_price(22000000, Decimal("0.7"), 2800000, 2600000)
    assert engine.round_price(ex, 500000) == 23000000
    assert engine.exact_price(22000000, Decimal(0), 9999990, 2600000) == 22000000
