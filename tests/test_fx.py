import io
import json
import urllib.error
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from borna_pricing import config, fx
from borna_pricing.clock import TEHRAN
from borna_pricing.hours import BusinessHours

from conftest import FIXTURES, write_settings

SAMPLE = (FIXTURES / "alanchand_currencies_sample.json").read_text(encoding="utf-8")
ERROR = (FIXTURES / "alanchand_error.json").read_text(encoding="utf-8")
PT_SAMPLE = (FIXTURES / "pricetoday_usd_sample.json").read_text(encoding="utf-8")
UPDATED_UTC = datetime(2026, 10, 31, 9, 50, 6, tzinfo=timezone.utc)
PT_UPDATED_UTC = datetime(2026, 10, 31, 16, 29, 55, tzinfo=timezone.utc)
FAKE_TOKEN = "TESTTOKEN123456"


def _cfg(tmp_path, source=None, alanchand=None, pricetoday=None):
    cfg = config.load_config(write_settings(tmp_path))
    return replace(cfg, source=replace(cfg.source, **(source or {})),
                   alanchand=replace(cfg.alanchand, **(alanchand or {})),
                   pricetoday=replace(cfg.pricetoday, **(pricetoday or {})))


def _pt(tmp_path, **source):
    return _cfg(tmp_path, source={"provider": "pricetoday", **source})


def test_sample_response_usd_sell_is_toman(tmp_path):
    res = fx.parse_payload(SAMPLE, _cfg(tmp_path))
    assert res.ok and res.value_toman == 273400
    assert res.source_updated_raw == "2026-10-31 13:20:06"
    assert res.source_updated_utc == UPDATED_UTC
    assert '"sell": 273400' in res.raw_entry


def test_other_slug_field_and_unit(tmp_path):
    assert fx.parse_payload(SAMPLE, _cfg(tmp_path, alanchand={"slug": "usd-hav"})).value_toman == 272700
    assert fx.parse_payload(SAMPLE, _cfg(tmp_path, alanchand={"field": "buy"})).value_toman == 270650
    assert fx.parse_payload(SAMPLE, _cfg(tmp_path, alanchand={"unit": "rial"})).value_toman == 27340


def test_api_error_and_bad_payloads(tmp_path):
    a = _cfg(tmp_path)
    res = fx.parse_payload(ERROR, a)
    assert not res.ok and "توکن یافت نشد" in res.message
    assert not fx.parse_payload("<html>", a).ok
    assert "usd" in fx.parse_payload('{"eur": {"sell": 1}}', a).message
    assert not fx.parse_payload('{"usd": {"sell": "N/A"}}', a).ok
    assert fx.parse_payload('{"usd": {"sell": "۲۷۳٬۴۰۰"}}', a).value_toman == 273400


def test_file_mode_reads_sample(tmp_path):
    res = fx.fetch(_cfg(tmp_path))
    assert res.ok and res.value_toman == 273400 and res.reached and "آزمون" in res.message


def test_live_mode_without_token_does_not_call_network(tmp_path):
    def boom(url, timeout):
        raise AssertionError("network must not be called")
    res = fx.fetch(_cfg(tmp_path, source={"mode": "live"}), http_get=boom)
    assert not res.ok and "توکن" in res.message


def test_live_mode_url_and_success(tmp_path):
    seen = {}

    def fake_get(url, timeout):
        seen["url"], seen["timeout"] = url, timeout
        return 200, SAMPLE
    res = fx.fetch(_cfg(tmp_path, source={"mode": "live"}, alanchand={"token": FAKE_TOKEN}), http_get=fake_get)
    assert res.ok and res.value_toman == 273400 and res.http_status == 200
    assert seen["url"] == f"https://api.alanchand.com/?type=currencies&token={FAKE_TOKEN}"
    assert seen["timeout"] == 20


def test_http_error_keeps_api_message_and_hides_token(tmp_path):
    def fake_get(url, timeout):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, io.BytesIO(ERROR.encode()))
    res = fx.fetch(_cfg(tmp_path, source={"mode": "live"}, alanchand={"token": FAKE_TOKEN}), http_get=fake_get)
    assert not res.ok and res.reached and res.http_status == 401
    assert "401" in res.message and "توکن یافت نشد" in res.message


def test_network_error_is_masked_and_marked_unreached(tmp_path):
    def fake_get(url, timeout):
        raise urllib.error.URLError(f"cannot open {url}")
    res = fx.fetch(_cfg(tmp_path, source={"mode": "live"}, alanchand={"token": FAKE_TOKEN}), http_get=fake_get)
    assert not res.ok and not res.reached
    assert FAKE_TOKEN not in res.message and "***" in res.message


def test_pricetoday_sample_is_rial_with_utc_time(tmp_path):
    cfg = _pt(tmp_path)
    res = fx.parse_payload(PT_SAMPLE, cfg)
    assert res.ok and res.value_toman == 270500
    assert res.source_updated_raw == "2026-10-31T16:29:55.000Z"
    assert res.source_updated_utc == PT_UPDATED_UTC
    assert json.loads(res.raw_entry)["high"] == 2735100
    assert fx.series(cfg) == ("usd", "price") and fx.label(cfg) == "priceto.day"
    assert fx.parse_payload(PT_SAMPLE, _cfg(tmp_path, source={"provider": "pricetoday"},
                                            pricetoday={"unit": "toman"})).value_toman == 2705000


def test_pricetoday_other_shapes_and_errors(tmp_path):
    cfg = _pt(tmp_path)
    assert fx.parse_payload("[" + PT_SAMPLE + "]", cfg).value_toman == 270500
    res = fx.parse_payload('{"success": false, "message": "No data found for this base and symbol"}', cfg)
    assert not res.ok and "No data found" in res.message
    assert not fx.parse_payload('{"success": true, "price": 0, "time": "2026-10-31T16:29:55Z"}', cfg).ok
    assert not fx.parse_payload("[]", cfg).ok
    no_time = fx.parse_payload('{"success": true, "price": "2,705,000"}', cfg)
    assert no_time.ok and no_time.value_toman == 270500 and no_time.source_updated_utc is None
    assert fx.parse_utc("2026-10-31T16:29:55Z") == PT_UPDATED_UTC
    assert fx.parse_utc("2026-10-31T19:59:55.5+03:30") == PT_UPDATED_UTC + timedelta(milliseconds=500)
    assert fx.parse_utc("2026-10-31 16:29:55") == PT_UPDATED_UTC
    assert fx.parse_utc("yesterday") is None and fx.parse_utc(None) is None


def test_pricetoday_live_needs_no_token(tmp_path):
    seen = {}

    def fake_get(url, timeout):
        seen["url"], seen["timeout"] = url, timeout
        return 200, PT_SAMPLE
    res = fx.fetch(_pt(tmp_path, mode="live", timeout_seconds=7), http_get=fake_get)
    assert res.ok and res.value_toman == 270500 and res.reached
    assert seen == {"url": "https://api.priceto.day/v1/latest/irr/usd", "timeout": 7}


def test_pricetoday_http_and_network_errors(tmp_path):
    def not_found(url, timeout):
        raise urllib.error.HTTPError(url, 404, "Not Found", {},
                                     io.BytesIO(b'{"success": false, "message": "No data found"}'))
    res = fx.fetch(_pt(tmp_path, mode="live"), http_get=not_found)
    assert not res.ok and res.reached and "404" in res.message and "No data found" in res.message

    def offline(url, timeout):
        raise urllib.error.URLError("timed out")
    res = fx.fetch(_pt(tmp_path, mode="live"), http_get=offline)
    assert not res.ok and not res.reached and "priceto.day" in res.message


def test_pricetoday_file_mode(tmp_path):
    res = fx.fetch(_pt(tmp_path))
    assert res.ok and res.value_toman == 270500 and "آزمون" in res.message


def _guards(tmp_path):
    return config.load_config(write_settings(tmp_path)).guards


def _ok(value, updated=UPDATED_UTC):
    return fx.FetchResult(True, "", value, "x", updated, "{}", 200, True)


def test_guard_accepts_small_move(tmp_path):
    d = fx.evaluate(_ok(273400), 270000, "آخرین نرخ پذیرفته‌شده", UPDATED_UTC + timedelta(minutes=13), _guards(tmp_path))
    assert d.status == "ok" and d.accepted and d.jump.quantize(Decimal("0.0001")) == Decimal("0.0126")


def test_guard_holds_jump_over_three_percent(tmp_path):
    d = fx.evaluate(_ok(273400), 260000, "نرخ مبنا", UPDATED_UTC + timedelta(minutes=13), _guards(tmp_path))
    assert d.status == "held" and not d.accepted
    assert "۵٫۱۵ درصد بالاتر از نرخ مبنا" in d.message and "سقف جهش ۳ درصد" in d.message


def test_guard_rejects_stale_rate(tmp_path):
    d = fx.evaluate(_ok(273400), 273000, "x", UPDATED_UTC + timedelta(minutes=181), _guards(tmp_path))
    assert d.status == "stale" and not d.accepted
    d = fx.evaluate(_ok(273400), 273000, "x", UPDATED_UTC + timedelta(minutes=179), _guards(tmp_path))
    assert d.status == "ok"


def test_guard_counts_age_in_business_hours(tmp_path):
    cfg = config.load_config(write_settings(tmp_path))
    bh = BusinessHours(cfg.hours)
    rate = _ok(270500, PT_UPDATED_UTC)
    sun = lambda h, m: datetime(2026, 11, 1, h, m, tzinfo=TEHRAN)
    assert fx.evaluate(rate, 271600, "x", sun(9, 30), cfg.guards, bh).status == "ok"
    assert fx.evaluate(rate, 271600, "x", sun(12, 0), cfg.guards, bh).status == "ok"
    d = fx.evaluate(rate, 271600, "x", sun(12, 30), cfg.guards, bh)
    assert d.status == "stale" and "۲۱۰ دقیقه از ساعت کاری" in d.message and "۱۹:۵۹ دیروز" in d.message
    assert fx.evaluate(rate, 271600, "x", sun(9, 30), cfg.guards).status == "stale"
    thu = _ok(270500, datetime(2026, 11, 5, 12, 50, tzinfo=TEHRAN))
    assert fx.evaluate(thu, 271600, "x", datetime(2026, 11, 7, 9, 20, tzinfo=TEHRAN), cfg.guards, bh).status == "ok"


def test_guard_rejects_rial_value_as_out_of_range(tmp_path):
    d = fx.evaluate(_ok(2734000), 273000, "x", UPDATED_UTC, _guards(tmp_path))
    assert d.status == "out_of_range" and "واحد" in d.message


def test_guard_holds_rate_without_timestamp_and_notes_future_time(tmp_path):
    d = fx.evaluate(_ok(273400, updated=None), 273000, "x", UPDATED_UTC, _guards(tmp_path))
    assert d.status == "held"
    d = fx.evaluate(_ok(273400), 273000, "x", UPDATED_UTC - timedelta(minutes=30), _guards(tmp_path))
    assert d.status == "ok" and "ساعت" in d.message


def test_guard_passes_fetch_errors_through(tmp_path):
    d = fx.evaluate(fx.FetchResult(False, "API خطا داد"), 273000, "x", UPDATED_UTC, _guards(tmp_path))
    assert d.status == "error" and d.message == "API خطا داد"
