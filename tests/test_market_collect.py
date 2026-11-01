import gzip

import pytest

from borna_pricing.market import evidence, extract, fetcher
from borna_pricing.market.fetcher import FetchBlocked, Fetcher, Response

from conftest import FIXTURES

MARKET = FIXTURES / "market"
HTML = {"content-type": "text/html; charset=utf-8"}


def resolver(table=None):
    table = table or {}

    def resolve(host, port):
        return table.get(host, ["93.184.216.34"])
    return resolve


class FakeWeb:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def __call__(self, url, headers, timeout, max_bytes):
        self.calls.append((url, headers))
        if url not in self.pages:
            return Response(404, {"content-type": "text/plain"}, b"not found")
        return self.pages[url]


def make(pages, table=None, **kw):
    sleeps = []
    t = [100.0]

    def sleep(s):
        sleeps.append(s)
        t[0] += s
    web = FakeWeb(pages)
    f = Fetcher("BornaMarketBot/1.2", min_delay=3, resolver=resolver(table), transport=web, sleep=sleep,
                clock=lambda: t[0], **kw)
    return f, web, sleeps


def page(body, status=200, headers=None):
    return Response(status, headers or HTML, body.encode("utf-8") if isinstance(body, str) else body)


def test_domain_normalization():
    assert fetcher.normalize_domain("https://www.Basalam.com/cat/x") == "basalam.com"
    assert fetcher.normalize_domain("emalls.ir") == "emalls.ir"
    with pytest.raises(FetchBlocked):
        fetcher.normalize_domain("localhost")
    with pytest.raises(FetchBlocked):
        fetcher.normalize_domain("not a domain")


@pytest.mark.parametrize("url", [
    "ftp://shop.example.ir/x", "https://evil.example.com/x", "https://shop.example.ir.evil.com/",
    "https://user:pw@shop.example.ir/", "https://shop.example.ir:8080/", "javascript:alert(1)",
])
def test_ssrf_guard_rejects_outside_urls(url):
    f, _, _ = make({})
    with pytest.raises(FetchBlocked):
        f.check_url(url, "shop.example.ir")


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "192.168.1.5", "169.254.169.254", "::1", "fd00::1",
                                "0.0.0.0", "::ffff:10.0.0.1"])
def test_ssrf_guard_rejects_private_addresses(ip):
    f, _, _ = make({}, {"shop.example.ir": [ip]})
    with pytest.raises(FetchBlocked):
        f.check_url("https://shop.example.ir/", "shop.example.ir")


def test_subdomain_allowed_and_fragment_dropped():
    f, _, _ = make({})
    assert f.check_url("https://m.shop.example.ir/p?a=1#x", "shop.example.ir") == "https://m.shop.example.ir/p?a=1"


def test_robots_rules():
    robots = "User-agent: *\nDisallow: /private\n\nUser-agent: BornaMarketBot\nDisallow: /cart\nCrawl-delay: 7\n"
    f, web, sleeps = make({"https://shop.example.ir/robots.txt": page(robots, headers={"content-type": "text/plain"}),
                           "https://shop.example.ir/list": page("<html>ok</html>")})
    assert f.get("https://shop.example.ir/list", "shop.example.ir").status == 200
    with pytest.raises(FetchBlocked, match="robots"):
        f.get("https://shop.example.ir/cart/1", "shop.example.ir")
    assert sleeps and max(sleeps) >= 7 - 1e-9
    assert all(h["User-Agent"] == "BornaMarketBot/1.2" for _, h in web.calls)


def test_robots_missing_allows_and_server_error_blocks():
    f, _, _ = make({"https://a.example.ir/x": page("<html></html>")})
    assert f.get("https://a.example.ir/x", "a.example.ir").status == 200
    f, _, _ = make({"https://b.example.ir/robots.txt": page("oops", 503),
                    "https://b.example.ir/x": page("<html></html>")})
    with pytest.raises(FetchBlocked, match="robots"):
        f.get("https://b.example.ir/x", "b.example.ir")


def test_redirects_are_checked_each_hop():
    f, _, _ = make({"https://shop.example.ir/a": page("", 302, {"location": "/b"}),
                    "https://shop.example.ir/b": page("<html>b</html>"),
                    "https://shop.example.ir/c": page("", 301, {"location": "http://169.254.169.254/meta"})})
    assert f.get("https://shop.example.ir/a", "shop.example.ir").url == "https://shop.example.ir/b"
    with pytest.raises(FetchBlocked):
        f.get("https://shop.example.ir/c", "shop.example.ir")


def test_size_type_and_status_limits():
    f, _, _ = make({"https://shop.example.ir/big": page("x" * 2000),
                    "https://shop.example.ir/img": page(b"\x89PNG", headers={"content-type": "image/png"}),
                    "https://shop.example.ir/gone": page("no", 410)}, max_bytes=1000)
    with pytest.raises(FetchBlocked, match="حجم"):
        f.get("https://shop.example.ir/big", "shop.example.ir")
    with pytest.raises(FetchBlocked, match="نوع محتوا"):
        f.get("https://shop.example.ir/img", "shop.example.ir")
    with pytest.raises(FetchBlocked, match="410"):
        f.get("https://shop.example.ir/gone", "shop.example.ir")


def test_polite_delay_between_requests_same_host():
    f, _, sleeps = make({"https://shop.example.ir/1": page("<html></html>"),
                         "https://shop.example.ir/2": page("<html></html>")})
    f.get("https://shop.example.ir/1", "shop.example.ir", delay=5)
    f.get("https://shop.example.ir/2", "shop.example.ir", delay=5)
    assert sum(sleeps) >= 10 - 1e-9


def test_charset_detection():
    p = fetcher.Page("u", 200, "text/html", '<meta charset="windows-1256">'.encode() + "سلام".encode("cp1256"))
    assert "سلام" in p.text()


def test_evidence_store_roundtrip_and_dedup(tmp_path):
    store = evidence.EvidenceStore(tmp_path)
    sha, rel, n = store.put(b"<html>1</html>", "html")
    sha2, rel2, _ = store.put(b"<html>1</html>", "html")
    assert (sha, rel) == (sha2, rel2) and n == 14
    assert store.read(rel) == b"<html>1</html>" and store.verify(rel, sha)
    assert gzip.decompress((tmp_path / rel).read_bytes()) == b"<html>1</html>"
    with pytest.raises(ValueError):
        store.path("../../etc/passwd")
    with pytest.raises(ValueError):
        store.put(b"x", "exe")


def test_upload_sniffing():
    assert evidence.sniff_upload(b"\x89PNG\r\n\x1a\nxxx") == "png"
    assert evidence.sniff_upload(b"\xff\xd8\xff\xe0xx") == "jpg"
    assert evidence.sniff_upload(b"%PDF-1.7") == "pdf"
    assert evidence.sniff_upload("قیمت تلفنی".encode(), "note.txt") == "txt"
    assert evidence.sniff_upload(b"<svg onload=alert(1)>", "x.svg") is None
    assert evidence.sniff_upload(b"<html>", "x.html") is None


def test_jsonld_itemlist_extraction():
    html = (MARKET / "synthetic_itemlist.html").read_text(encoding="utf-8")
    items, warnings = extract.extract(html, "https://shop.example.ir/cat/o2")
    assert len(items) == 4 and len(warnings) == 1
    a, b, c, d = items
    assert a.price == "3400000" and a.currency == "IRR" and a.availability == "in_stock"
    assert a.url == "https://shop.example.ir/p/a-100" and a.ext_id == "A-100" and a.brand == "NTK"
    assert a.seller == "فروشگاه نمونه الف"
    assert b.price == "2950000" and b.list_price == "3300000" and b.currency == "IRT"
    assert extract.unit_from_currency(b.currency, "rial") == "toman"
    assert c.availability == "out_of_stock" and c.price == "0"
    assert extract.ext_key(a, "https://shop.example.ir/cat/o2") == "id:A-100"


def test_jsonld_graph_product_and_og_fallback():
    items, _ = extract.extract((MARKET / "synthetic_product.html").read_text(encoding="utf-8"), "https://s.example.ir/p/1")
    assert len(items) == 1 and items[0].part_no == "0258007342" and items[0].price == "6,200,000"
    items, _ = extract.extract((MARKET / "synthetic_og.html").read_text(encoding="utf-8"), "https://shop.example.ir/item/77")
    assert len(items) == 1 and items[0].title.endswith("& OZA603-R2") and items[0].price == "2,850,000"
    assert items[0].availability == "in_stock"


def test_regex_parser():
    html = (MARKET / "synthetic_cards.html").read_text(encoding="utf-8")
    rx = (r'<div class="card" data-id="(?P<id>\d+)"><a href="(?P<url>[^"]+)"><h3>(?P<title>.*?)</h3></a>'
          r'<span class="price">(?P<price>.*?)</span><span class="stock">(?P<availability>.*?)</span>')
    items, _ = extract.extract(html, "https://shop.example.ir/list", "regex", rx)
    assert [i.ext_id for i in items] == ["501", "502"]
    assert items[0].title == "سنسور اکسیژن زیمنسی سوکت مشکی NTK سیم 50 سانت"
    assert items[0].url == "https://shop.example.ir/item/501" and items[0].availability == "in_stock"
    assert items[1].price == "تماس بگیرید" and items[1].availability == "out_of_stock"
    with pytest.raises(ValueError):
        extract.validate_regex(r"(?P<title>x)")
    with pytest.raises(ValueError):
        extract.validate_regex(r"(?P<title>x)(?P<price>")
