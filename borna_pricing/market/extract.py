from __future__ import annotations

import html as htmllib
import json
import re
import urllib.parse
from dataclasses import dataclass

from .prices import detect_availability, schema_availability

PARSER_VERSION = "1.0"
MAX_ITEMS = 500
_LD = re.compile(r"<script[^>]+type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.S | re.I)
_META = re.compile(r"<meta\s+[^>]*>", re.I)
_ATTR = re.compile(r"([a-zA-Z_:\-]+)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)")
_TAGS = re.compile(r"<[^>]+>")
REGEX_GROUPS = ("title", "price")


@dataclass
class RawItem:
    title: str
    price: str | None = None
    currency: str | None = None
    availability: str = "unknown"
    url: str | None = None
    ext_id: str | None = None
    brand: str | None = None
    seller: str | None = None
    list_price: str | None = None
    part_no: str | None = None


def clean(text) -> str:
    s = htmllib.unescape(_TAGS.sub(" ", str(text or "")))
    return re.sub(r"\s+", " ", s).strip()


def _types(obj) -> set[str]:
    t = obj.get("@type")
    items = t if isinstance(t, list) else [t]
    return {str(x).split("/")[-1].lower() for x in items if x}


def _name(value) -> str | None:
    if isinstance(value, dict):
        value = value.get("name")
    if isinstance(value, list):
        value = value[0] if value else None
    s = clean(value) if value not in (None, "") else ""
    return s or None


def _offer_fields(offers):
    if isinstance(offers, list):
        offers = next((o for o in offers if isinstance(o, dict)), None)
    if not isinstance(offers, dict):
        return None, None, "unknown", None, None
    types = _types(offers)
    price = offers.get("price")
    high = None
    if "aggregateoffer" in types or price in (None, ""):
        price = offers.get("lowPrice", price)
        high = offers.get("highPrice")
    spec = offers.get("priceSpecification")
    if price in (None, "") and isinstance(spec, dict):
        price = spec.get("price")
    currency = offers.get("priceCurrency") or (spec.get("priceCurrency") if isinstance(spec, dict) else None)
    return (None if price in (None, "") else str(price), currency, schema_availability(offers.get("availability")),
            _name(offers.get("seller")), None if high in (None, "") else str(high))


def _walk(obj, out: list, base_url: str, depth: int = 0) -> None:
    if depth > 8 or len(out) >= MAX_ITEMS:
        return
    if isinstance(obj, list):
        for x in obj:
            _walk(x, out, base_url, depth + 1)
        return
    if not isinstance(obj, dict):
        return
    types = _types(obj)
    if "product" in types:
        title = _name(obj.get("name"))
        if title:
            price, currency, avail, seller, high = _offer_fields(obj.get("offers"))
            offers = obj.get("offers")
            url = obj.get("url") or (offers.get("url") if isinstance(offers, dict) else None)
            url = urllib.parse.urljoin(base_url, str(url)) if url else None
            out.append(RawItem(title=title, price=price, currency=currency, availability=avail, url=url,
                               ext_id=str(obj.get("sku") or obj.get("productID") or obj.get("@id") or "") or None,
                               brand=_name(obj.get("brand")), seller=seller, list_price=high,
                               part_no=str(obj.get("mpn") or "") or None))
        return
    if "itemlist" in types:
        for el in obj.get("itemListElement") or []:
            _walk(el.get("item", el) if isinstance(el, dict) else el, out, base_url, depth + 1)
        return
    if "@graph" in obj:
        _walk(obj["@graph"], out, base_url, depth + 1)
        return
    for key in ("mainEntity", "about"):
        if isinstance(obj.get(key), (dict, list)):
            _walk(obj[key], out, base_url, depth + 1)


def from_jsonld(html: str, base_url: str) -> tuple[list[RawItem], list[str]]:
    items: list[RawItem] = []
    warnings = []
    for block in _LD.findall(html):
        text = block.strip().removeprefix("<!--").removesuffix("-->").strip().rstrip(";")
        try:
            data = json.loads(text)
        except ValueError:
            warnings.append("یک بلوک JSON-LD خراب بود و نادیده گرفته شد.")
            continue
        _walk(data, items, base_url)
    return items[:MAX_ITEMS], warnings


def from_meta(html: str, base_url: str) -> list[RawItem]:
    meta = {}
    for tag in _META.findall(html[:200_000]):
        attrs = {k.lower(): v.strip("\"'") for k, v in _ATTR.findall(tag)}
        key = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").lower()
        if key and "content" in attrs and key not in meta:
            meta[key] = htmllib.unescape(attrs["content"])
    price = meta.get("product:price:amount") or meta.get("og:price:amount") or meta.get("price")
    title = meta.get("og:title") or meta.get("twitter:title")
    if not title or not price:
        return []
    url = meta.get("og:url")
    return [RawItem(title=clean(title), price=price,
                    currency=meta.get("product:price:currency") or meta.get("og:price:currency"),
                    availability=schema_availability(meta.get("product:availability") or meta.get("og:availability")),
                    url=urllib.parse.urljoin(base_url, url) if url else base_url, ext_id=None,
                    brand=meta.get("product:brand"))]


def validate_regex(pattern: str) -> re.Pattern:
    if not pattern or len(pattern) > 2000:
        raise ValueError("الگوی regex خالی یا بیش از حد طولانی است.")
    try:
        rx = re.compile(pattern, re.S | re.I)
    except re.error as exc:
        raise ValueError(f"الگوی regex معتبر نیست: {exc}") from None
    missing = [g for g in REGEX_GROUPS if g not in rx.groupindex]
    if missing:
        raise ValueError("الگو باید گروه‌های نام‌دار (?P<title>...) و (?P<price>...) داشته باشد.")
    return rx


def from_regex(html: str, base_url: str, pattern: str) -> list[RawItem]:
    rx = validate_regex(pattern)
    out = []
    for m in rx.finditer(html):
        g = m.groupdict()
        title = clean(g.get("title"))
        if not title:
            continue
        url = clean(g.get("url")) or None
        out.append(RawItem(title=title, price=clean(g.get("price")) or None,
                           availability=detect_availability(clean(g.get("availability"))),
                           url=urllib.parse.urljoin(base_url, url) if url else None,
                           ext_id=clean(g.get("id")) or None, seller=clean(g.get("seller")) or None,
                           list_price=clean(g.get("list_price")) or None))
        if len(out) >= MAX_ITEMS:
            break
    return out


def extract(html: str, base_url: str, parser: str = "auto", item_regex: str | None = None) -> tuple[list[RawItem], list[str]]:
    if parser == "regex":
        return from_regex(html, base_url, item_regex or ""), []
    items, warnings = from_jsonld(html, base_url)
    if not items:
        items = from_meta(html, base_url)
    return items, warnings


def ext_key(item: RawItem, page_url: str) -> str:
    if item.ext_id:
        return f"id:{item.ext_id}"[:300]
    if item.url and item.url.rstrip("/") != page_url.rstrip("/"):
        parts = urllib.parse.urlsplit(item.url)
        return f"url:{parts.netloc.lower()}{parts.path}"[:300]
    return "title:" + re.sub(r"\s+", " ", item.title.strip().lower())[:280]


def unit_from_currency(currency: str | None, default: str) -> str:
    c = (currency or "").strip().upper()
    if c in ("IRT", "TOMAN", "TMN"):
        return "toman"
    return default
