import pytest

from borna_pricing import importer
from borna_pricing.market import catalog, matching, prices, textnorm as tn

from conftest import ROOT


@pytest.fixture(scope="module")
def profiles():
    items, errors, _ = importer.read_products(ROOT / "data" / "Borna_Step2-3_Products_Pricing.xlsx")
    assert not errors
    return [catalog.product_profile(it) for it in items]


def best(profiles, title):
    res = matching.match_listing(tn.parse_title(title), profiles)
    return res, {v.product_key: v for v in res}


@pytest.mark.parametrize("raw,expected", [
    ("258006027", "0258006027"), ("0 258 006 027", "0258006027"), ("0258.006.027", "0258006027"),
    ("oza603-N3", "OZA603-N3"), ("OZA 632 SZ2", "OZA632-SZ2"), ("smw250308", "SMW250308"),
    ("J523611061", "J523611061"), ("۲۵۸۰۰۶۰۲۷", "0258006027"),
])
def test_part_number_normalization(raw, expected):
    assert tn.normalize_pn(raw) == expected


def test_part_numbers_found_inside_titles():
    t = "سنسور اکسیژن بوش طرح اصلی ام ای هفده me17 با پارت نامبر 258030289 و OZA660-EE4"
    assert tn.find_part_numbers(t) == ["OZA660-EE4", "0258030289"]
    assert tn.find_part_numbers("پژو 405 و پراید 111") == []


def test_title_attributes():
    a = tn.parse_title("سنسور اکسیژن زیمنس سوکت طوسی NTK اصل ژاپن OZA632-SZ2 سیم 50 سانت")
    assert (a["ecu"], a["socket_color"], a["length_mm"], a["design"], a["tier"]) == ("siemens", "gray", 500, "ntk", "genuine")
    assert a["wire_count"] is None
    b = tn.parse_title("سنسور اکسیژن بوش ساژم سیم کوتاه آبی")
    assert (b["ecu"], b["length_class"], b["wire_color"], b["socket_color"]) == ("sagem", "short", "blue", None)
    c = tn.parse_title("سنسور اکسیژن بوش ساژم سیم متوسط سوکت آبی")
    assert (c["length_class"], c["socket_color"], c["wire_color"]) == ("medium", "blue", None)
    d = tn.parse_title("سنسور اکسیژن پایینی ME17 بوش اورجینال پژو 206 و EF7 با لیبل")
    assert (d["ecu"], d["position"], d["tier"]) == ("me17", "downstream", "genuine")
    assert d["vehicles"] == ["p206", "ef7"]
    assert tn.parse_title("سنسور اکسیژن طرح بوش پنج سیم EF7")["wire_count"] == 5
    assert tn.parse_title("سنسور اکسیژن 4 سیم")["wire_count"] == 4
    assert tn.parse_title("سنسور اکسيژن ntk ژاپن اصلی، زیمنسی و ساژم")["ecus"] == ["siemens", "sagem"]
    assert tn.parse_title("سنسور اکسیژن نسل جدید زیمنس")["generation"] == "new"


@pytest.mark.parametrize("title,tier", [
    ("سنسور اکسیژن بوش طرح اصلی", "aftermarket"), ("سنسور اکسیژن NTK اصل ژاپن", "genuine"),
    ("سنسور اکسیژن شرکتی سمند", "oem"), ("سنسور اکسیژن زیمنس", "unknown"), ("سنسور اکسیژن های کپی", "aftermarket"),
])
def test_tier(title, tier):
    assert tn.find_tier(title) == tier


def test_family_excludes_tools_and_other_sensors():
    assert tn.is_o2_sensor("سنسور اکسیژن زیمنس")
    assert not tn.is_o2_sensor("بکس سنسور اکسیژن پرو کد BPR-00779")
    assert not tn.is_o2_sensor("سنسور دریچه گاز زیمنس پژو 405")
    assert not tn.is_o2_sensor("سنسور دمای آب")


@pytest.mark.parametrize("text,unit,kind,rial", [
    ("۲٬۷۰۰٬۰۰۰ تومان", "rial", "price", 27_000_000),
    ("27,000,000", "rial", "price", 27_000_000),
    ("3,400,000", "toman", "price", 34_000_000),
    ("3.5 میلیون تومان", "rial", "price", 35_000_000),
    ("۳/۵ میلیون", "toman", "price", 35_000_000),
    ("قیمت: 3.400.000 تومان", "rial", "price", 34_000_000),
    ("3500000 ریال", "toman", "price", 3_500_000),
    ("تماس بگیرید", "toman", "quote_only", None),
    ("0", "toman", "quote_only", None),
    ("ناموجود", "toman", "quote_only", None),
    ("abc", "toman", None, None),
])
def test_price_parsing(text, unit, kind, rial):
    p = prices.parse_price(text, unit)
    assert (p.kind, p.value_rial) == (kind, rial)


def test_availability_and_guards():
    assert prices.detect_availability("ناموجود") == "out_of_stock"
    assert prices.detect_availability("موجود در انبار") == "in_stock"
    assert prices.schema_availability("https://schema.org/OutOfStock") == "out_of_stock"
    assert prices.schema_availability("http://schema.org/InStock") == "in_stock"
    lo, hi = prices.usd_bounds_rial(2, 60, 260_000)
    assert (lo, hi) == (5_200_000, 156_000_000)
    assert prices.in_range(27_000_000, lo, hi)
    assert not prices.in_range(270_000_000, lo, hi)
    assert not prices.in_range(2_700_000, lo, hi)
    assert prices.jump(30, 20) == pytest.approx(0.5)


def test_catalog_profiles(profiles):
    by = {p["key"]: p for p in profiles}
    assert len(by) == 27
    assert by["FIB101-480"]["socket_color"] == "gray" and by["FIB101-480"]["length_mm"] == 480
    assert by["FIB206-350"]["wire_count"] == 5 and by["FIB206-350"]["vehicles"] == ["ef7"]
    assert by["FIB204-480"]["ecu"] == "sagem" and by["FIB208-480"]["ecu"] == "siemens"
    assert by["TMP-01"]["generation"] == "new" and by["TMP-01"]["length_class"] == "short"
    assert by["FIB201-400"]["part_nos"] == ["0258030289"] and by["FIB201-400"]["design"] == "bosch"


def test_manual_specs_override_and_extend(profiles):
    item = {"key": "X", "name": "سنسور اکسیژن زیمنسی", "part_no_norm": "OZA632-SZ1"}
    specs = [{"attr": "xref", "value": "0258006999"}, {"attr": "wire_count", "value": "4"},
             {"attr": "vehicle", "value": "pride"}]
    p = catalog.product_profile(item, specs)
    assert p["part_nos"] == ["OZA632-SZ1", "0258006999"] and p["wire_count"] == 4 and p["vehicles"] == ["pride"]
    assert catalog.spec_value("xref", "0 258 006 027") == "0258006027"
    assert catalog.spec_value("ecu", "زیمنس") == "siemens"
    assert catalog.spec_value("socket_color", "خاکستری") == "gray"
    assert catalog.spec_value("vehicle", "پژو ۴۰۵") == "p405"
    with pytest.raises(ValueError):
        catalog.spec_value("length_mm", "پنج")
    with pytest.raises(ValueError):
        catalog.spec_value("ecu", "زیمنس و ساژم")


def test_gold_exact_part_number_is_strong(profiles):
    res, by = best(profiles, "سنسور اکسیژن زیمنس سوکت طوسی NTK اصل ژاپن OZA632-SZ2 سیم 50 سانت")
    assert res[0].product_key == "FIB101-480" and res[0].verdict == "strong"
    assert len(res) == 1


def test_gold_siemens_vs_sagem_is_rejected(profiles):
    owners = matching.pn_owners(profiles)
    p = next(x for x in profiles if x["key"] == "FIB101-480")
    v = matching.evaluate(p, tn.parse_title("سنسور اکسیژن ساژم سوکت طوسی سیم 50 سانت"), owners)
    assert "ecu_mismatch" in v.reasons and v.hard


def test_gold_wire_count_mismatch_is_rejected(profiles):
    owners = matching.pn_owners(profiles)
    p = next(x for x in profiles if x["key"] == "FIB206-350")
    v = matching._finish(matching.evaluate(p, tn.parse_title("سنسور اکسیژن EF7 چهار سیم"), owners))
    assert v.verdict == "reject" and "wire_count_mismatch" in v.reasons


def test_gold_shared_part_number_resolved_by_ecu(profiles):
    res, by = best(profiles, "سنسور اکسیژن بوش ساژم 258006027")
    assert by["FIB204-480"].verdict == "strong"
    assert by["FIB208-480"].verdict == "reject" and by["TMP-06"].verdict == "reject"


def test_gold_shared_part_number_without_ecu_needs_review(profiles):
    res, by = best(profiles, "سنسور اکسیژن بوش 0258006027")
    assert {v.product_key for v in res} == {"FIB204-480", "FIB208-480", "TMP-06"}
    assert all(v.verdict == "review" and "pn_shared" in v.reasons for v in res)


def test_gold_socket_color_and_length_separate_skus(profiles):
    res, by = best(profiles, "سنسور اکسیژن زیمنسی سوکت مشکی NTK سیم 50 سانت")
    assert res[0].product_key == "FIB103-480" and res[0].verdict == "candidate"
    assert by["FIB101-480"].verdict == "reject" and "socket_mismatch" in by["FIB101-480"].reasons
    assert by["FIB102-250"].verdict == "reject" and "length_mismatch" in by["FIB102-250"].reasons
    assert by["FIB105-1100"].verdict == "reject"


def test_gold_length_within_tolerance(profiles):
    owners = matching.pn_owners(profiles)
    p = next(x for x in profiles if x["key"] == "FIB101-480")
    v = matching.evaluate(p, tn.parse_title("سنسور اکسیژن زیمنس سوکت طوسی سیم 50 سانت"), owners, tolerance_mm=30)
    assert "length_match" in v.reasons
    v = matching.evaluate(p, tn.parse_title("سنسور اکسیژن زیمنس سوکت طوسی سیم 50 سانت"), owners, tolerance_mm=10)
    assert "length_mismatch" in v.reasons


def test_gold_other_design_is_substitute_not_equivalent(profiles):
    res, by = best(profiles, "سنسور اکسیژن ME17 بوش سوکت مشکی")
    assert res[0].product_key == "FIB201-400" and res[0].verdict == "candidate"
    assert by["FIB109-400"].verdict == "review" and by["FIB109-400"].relation == "substitute"
    assert by["FIB202-400"].verdict == "reject"


def test_gold_part_number_of_other_product_rejects(profiles):
    res, by = best(profiles, "سنسور اکسیژن ME17 NTK سوکت مشکی OZA660-EE4")
    assert res[0].product_key == "FIB109-400" and res[0].verdict == "strong"
    assert "FIB201-400" not in by
    owners = matching.pn_owners(profiles)
    p = next(x for x in profiles if x["key"] == "FIB201-400")
    assert "pn_other" in matching.evaluate(p, tn.parse_title("سنسور اکسیژن OZA660-EE4"), owners).reasons


def test_gold_l90_duplicates_stay_in_review(profiles):
    res, by = best(profiles, "سنسور اکسیژن NTK ماشین L90 OZA603-R2")
    assert {v.product_key for v in res} == {"FIB108-500", "TMP-03", "TMP-04"}
    assert all(v.verdict == "review" for v in res)


def test_gold_vehicle_engine_and_wire_count(profiles):
    res, _ = best(profiles, "سنسور اکسیژن EF7 پنج سیم بوش")
    assert res[0].product_key == "FIB206-350" and res[0].verdict == "candidate"


def test_gold_not_o2_and_no_signal_have_no_candidates(profiles):
    assert best(profiles, "سنسور دریچه گاز زیمنس")[0] == []
    assert best(profiles, "بکس سنسور اکسیژن")[0] == []
    assert best(profiles, "سنسور اکسیژن سمند")[0] == []
