from __future__ import annotations

import ipaddress
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field

PAGE_TYPES = ("text/html", "application/xhtml+xml", "application/json", "application/ld+json")
MAX_REDIRECTS = 5


class FetchBlocked(Exception):
    pass


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes


@dataclass
class Page:
    url: str
    status: int
    content_type: str
    body: bytes
    charset: str | None = None

    def text(self) -> str:
        cs = self.charset or _sniff_charset(self.body) or "utf-8"
        try:
            return self.body.decode(cs, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


def _sniff_charset(body: bytes) -> str | None:
    m = re.search(rb"<meta[^>]+charset=[\"']?([A-Za-z0-9_\-]+)", body[:4096], re.I)
    return m.group(1).decode("ascii", "ignore").lower() if m else None


def normalize_domain(raw: str) -> str:
    s = str(raw or "").strip().lower()
    if "://" in s:
        s = urllib.parse.urlsplit(s).hostname or ""
    s = s.strip(".").removeprefix("www.")
    if not re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}", s):
        raise FetchBlocked(f"دامنه «{raw}» معتبر نیست؛ مثلاً basalam.com")
    return s


def host_allowed(host: str, domain: str) -> bool:
    host = (host or "").lower().strip(".")
    return host == domain or host.endswith("." + domain)


def _default_resolver(host: str, port: int) -> list[str]:
    return list({info[4][0] for info in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)})


def ip_is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
                                   or addr.is_reserved or addr.is_unspecified)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_transport(url: str, headers: dict, timeout: int, max_bytes: int) -> Response:
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        resp = exc
    try:
        body = resp.read(max_bytes + 1) if getattr(resp, "fp", None) is not None else b""
        hdrs = {k.lower(): v for k, v in resp.headers.items()} if resp.headers else {}
        return Response(getattr(resp, "status", None) or getattr(resp, "code", 0), hdrs, body)
    finally:
        try:
            resp.close()
        except Exception:
            pass


@dataclass
class Fetcher:
    user_agent: str
    timeout: int = 20
    max_bytes: int = 3_000_000
    min_delay: float = 3.0
    resolver: object = None
    transport: object = None
    sleep: object = time.sleep
    clock: object = time.monotonic
    requests: int = 0
    _robots: dict = field(default_factory=dict)
    _last: dict = field(default_factory=dict)

    @property
    def agent_token(self) -> str:
        return self.user_agent.split("/", 1)[0].strip() or "*"

    def check_url(self, url: str, domain: str) -> str:
        try:
            parts = urllib.parse.urlsplit(str(url).strip())
        except ValueError:
            raise FetchBlocked("نشانی معتبر نیست.") from None
        if parts.scheme not in ("http", "https"):
            raise FetchBlocked("فقط نشانی‌های http و https مجاز است.")
        if parts.username or parts.password or "@" in parts.netloc:
            raise FetchBlocked("نشانی نباید نام کاربری یا رمز داشته باشد.")
        host = (parts.hostname or "").lower()
        if not host or not host_allowed(host, domain):
            raise FetchBlocked(f"نشانی «{host or url}» بیرون از دامنه ثبت‌شده منبع ({domain}) است.")
        try:
            port = parts.port
        except ValueError:
            raise FetchBlocked("درگاه نشانی معتبر نیست.") from None
        if port not in (None, 80, 443):
            raise FetchBlocked("فقط درگاه‌های استاندارد ۸۰ و ۴۴۳ مجاز است.")
        try:
            ips = (self.resolver or _default_resolver)(host, port or (443 if parts.scheme == "https" else 80))
        except OSError as exc:
            raise FetchBlocked(f"نام دامنه {host} پیدا نشد: {exc}") from None
        if not ips or not all(ip_is_public(ip) for ip in ips):
            raise FetchBlocked(f"دامنه {host} به نشانی داخلی یا غیرعمومی اشاره می‌کند؛ دسترسی بسته شد.")
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))

    def _wait(self, host: str, delay: float) -> None:
        last = self._last.get(host)
        if last is not None:
            remaining = delay - (self.clock() - last)
            if remaining > 0:
                self.sleep(remaining)
        self._last[host] = self.clock()

    def _raw(self, url: str, domain: str, delay: float, accept: str) -> tuple[str, Response]:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            current = self.check_url(current, domain)
            host = urllib.parse.urlsplit(current).hostname
            self._wait(host, delay)
            self.requests += 1
            headers = {"User-Agent": self.user_agent, "Accept": accept, "Accept-Language": "fa,en;q=0.5"}
            try:
                resp = (self.transport or _default_transport)(current, headers, self.timeout, self.max_bytes)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                raise FetchBlocked(f"اتصال برقرار نشد: {getattr(exc, 'reason', exc)}") from None
            if resp.status in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                current = urllib.parse.urljoin(current, resp.headers["location"])
                continue
            return current, resp
        raise FetchBlocked("تغییر مسیر (redirect) بیش از حد.")

    def robots(self, url: str, domain: str, delay: float):
        parts = urllib.parse.urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base in self._robots:
            return self._robots[base]
        rp = urllib.robotparser.RobotFileParser()
        try:
            _, resp = self._raw(base + "/robots.txt", domain, delay, "text/plain")
            if 200 <= resp.status < 300:
                rp.parse(resp.body[: self.max_bytes].decode("utf-8", errors="replace").splitlines())
            elif 400 <= resp.status < 500:
                rp.allow_all = True
            else:
                rp.disallow_all = True
        except FetchBlocked:
            rp.disallow_all = True
        self._robots[base] = rp
        return rp

    def get(self, url: str, domain: str, delay: float | None = None) -> Page:
        delay = max(float(delay if delay is not None else self.min_delay), float(self.min_delay))
        url = self.check_url(url, domain)
        rp = self.robots(url, domain, delay)
        if not rp.can_fetch(self.agent_token, url):
            raise FetchBlocked("robots.txt این سایت خواندن این صفحه را اجازه نمی‌دهد.")
        cd = rp.crawl_delay(self.agent_token)
        if cd:
            delay = max(delay, float(cd))
        final, resp = self._raw(url, domain, delay, "text/html,application/xhtml+xml,application/json;q=0.9")
        if len(resp.body) > self.max_bytes:
            raise FetchBlocked("حجم صفحه بیش از سقف مجاز است.")
        if resp.status != 200:
            raise FetchBlocked(f"سایت پاسخ HTTP {resp.status} داد.")
        ctype = (resp.headers.get("content-type") or "").lower()
        mime = ctype.split(";", 1)[0].strip()
        if mime not in PAGE_TYPES:
            raise FetchBlocked(f"نوع محتوای «{mime or 'نامعلوم'}» صفحه وب نیست.")
        m = re.search(r"charset=([A-Za-z0-9_\-]+)", ctype)
        return Page(final, resp.status, mime, resp.body, m.group(1).lower() if m else None)
