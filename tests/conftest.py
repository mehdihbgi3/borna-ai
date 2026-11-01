import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from borna_pricing import clock


@pytest.fixture(autouse=True)
def _real_clock_after_test():
    yield
    clock.set_fixed_now(None)


def write_settings(tmp_path: Path, extra: str = "", provider: str = "alanchand") -> Path:
    sample = (FIXTURES / "alanchand_currencies_sample.json").as_posix()
    sample_pt = (FIXTURES / "pricetoday_usd_sample.json").as_posix()
    xlsx = (ROOT / "data" / "Borna_Step2-3_Products_Pricing.xlsx").as_posix()
    text = f"""
[source]
provider = {provider}
mode = file

[alanchand]
sample_file = {sample}

[pricetoday]
sample_file = {sample_pt}

[paths]
database = data/test.sqlite3
products_xlsx = {xlsx}
log_file = data/test.log
secret_key_file = data/secret.key
{extra}
"""
    (tmp_path / "data").mkdir(exist_ok=True)
    p = tmp_path / "settings.ini"
    p.write_text(text, encoding="utf-8")
    return p


class FakeWeb:
    def __init__(self, pages=None):
        self.pages = dict(pages or {})
        self.calls = []

    def __call__(self, url, headers, timeout, max_bytes):
        from borna_pricing.market.fetcher import Response
        self.calls.append(url)
        if url not in self.pages:
            return Response(404, {"content-type": "text/plain"}, b"not found")
        status, ctype, body = self.pages[url]
        return Response(status, {"content-type": ctype}, body.encode("utf-8") if isinstance(body, str) else body)


def fake_fetcher_factory(web):
    from borna_pricing.market.fetcher import Fetcher

    def make():
        return Fetcher("BornaMarketBot/1.2", min_delay=0, resolver=lambda h, p: ["93.184.216.34"], transport=web,
                       sleep=lambda s: None)
    return make
