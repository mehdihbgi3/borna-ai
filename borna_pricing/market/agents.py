from __future__ import annotations

from datetime import datetime

from ..service import ServiceError

LEVELS = {"L0": "فقط خواندن داده عمومی", "L1": "نوشتن داده داخلی با ثبت رویداد", "L2": "اقدام حساس با تأیید انسان"}


class Agent:
    key = ""
    label = ""
    description = ""
    tools: tuple = ()
    level = "L1"

    def due(self, ms, now: datetime) -> bool:
        return False

    def next_info(self, ms, now: datetime) -> str:
        return ""

    def run(self, ms, run_id: int, user: str) -> dict:
        raise NotImplementedError


class PriceWatcher(Agent):
    key = "price_watcher"
    label = "پایشگر قیمت رقبا"
    description = ("صفحه‌های منبع‌های مجاز را با رعایت robots.txt و فاصله بین درخواست‌ها می‌خواند، هر صفحه را با "
                   "SHA-256 به‌عنوان مدرک نگه می‌دارد، قیمت و موجودی را درمی‌آورد و قیمت مشکوک را قرنطینه می‌کند.")
    tools = ("خواندن صفحه از دامنه‌های ثبت‌شده", "ذخیره مدرک", "ثبت مشاهده قیمت")
    level = "L0"

    def due(self, ms, now):
        return bool(ms.due_sources(now))

    def next_info(self, ms, now):
        due = [s for s in ms.sources(include_manual=False) if s["due_at"] is not None]
        if not due:
            return "هیچ منبع مجاز و فعالی نیست."
        first = min(due, key=lambda s: s["due_at"])
        return f"نوبت بعد: {first['name']}"

    def run(self, ms, run_id, user):
        sources = ms.due_sources() if user == "system" else [
            s for s in ms.sources(include_manual=False)
            if s["due_at"] is not None and s["start_urls"]]
        if not sources:
            return {"_status": "skipped", "_message": "منبعی برای پایش نیست (منبع مجاز، فعال و دارای نشانی لازم است)."}
        out = {"sources": 0, "pages": 0, "observations": 0, "new_listings": 0, "quarantined": 0, "errors": []}
        failed = 0
        for s in sources:
            try:
                st = ms.collect_source(s["id"], user, run_id)
            except ServiceError as exc:
                out["errors"].append(f"{s['name']}: {exc}")
                failed += 1
                continue
            out["sources"] += 1
            for k in ("pages", "observations", "new_listings", "quarantined"):
                out[k] += st[k]
            out["errors"] += [f"{s['name']}: {e}" for e in st["errors"]]
            if st["pages"] == 0:
                failed += 1
        m = ms.match_pending()
        out["matching"] = m
        if failed and failed == len(sources):
            out["_status"] = "failed"
            out["_message"] = "هیچ منبعی خوانده نشد؛ خطاها را ببینید."
        elif failed or out["errors"]:
            out["_status"] = "partial"
        return out


class Matcher(Agent):
    key = "matcher"
    label = "تطبیق‌دهنده کالا"
    description = ("هر آگهی تازه را با قواعد فنی سنسور اکسیژن (پارت‌نامبر، ECU، سوکت، طول سیم، تعداد سیم، خودرو) با "
                   "کالاهای برنا مقایسه می‌کند. ناسازگاری قطعی را رد می‌کند و بقیه را برای تأیید کارشناس پیشنهاد می‌دهد؛ "
                   "خودش هیچ تطبیقی را تأیید نمی‌کند.")
    tools = ("خواندن کاتالوگ فنی", "ثبت پیشنهاد تطبیق")
    level = "L1"

    def due(self, ms, now):
        with ms.db() as c:
            return c.execute("SELECT 1 FROM external_products WHERE match_state='new' LIMIT 1").fetchone() is not None

    def next_info(self, ms, now):
        with ms.db() as c:
            n = c.execute("SELECT COUNT(*) FROM external_products WHERE match_state='new'").fetchone()[0]
        return f"{n} آگهی منتظر تطبیق" if n else "آگهی تازه‌ای منتظر نیست."

    def run(self, ms, run_id, user):
        st = ms.match_pending(user)
        if not st["listings"]:
            st["_status"] = "skipped"
            st["_message"] = "آگهی تازه‌ای برای تطبیق نبود."
        return st


class MarketAnalyst(Agent):
    key = "market_analyst"
    label = "تحلیلگر بازار"
    description = ("هر هفته جایگاه قیمت هر کالای برنا را در برابر رقبا (جدا برای عمده و خرده، هم‌طرح و جایگزین)، "
                   "تغییر قیمت رقبا در برابر تغییر دلار، رقبای ناموجود و آگهی‌های تازه را در یک گزارش جمع می‌کند.")
    tools = ("خواندن قیمت‌های تأییدشده", "خواندن تاریخچه دلار", "ساخت گزارش")
    level = "L1"

    def due(self, ms, now):
        slot = ms.report_due_at(now)
        last = ms.last_report_at()
        if now < slot or (last is not None and last >= slot):
            return False
        with ms.db() as c:
            return c.execute("SELECT 1 FROM matches WHERE status='approved' LIMIT 1").fetchone() is not None

    def next_info(self, ms, now):
        from datetime import timedelta

        from .. import fmt
        slot = ms.report_due_at(now)
        last = ms.last_report_at()
        nxt = slot if (last is None or last < slot) else slot + timedelta(days=7)
        return f"گزارش بعدی: {fmt.jdatetime(nxt)}"

    def run(self, ms, run_id, user):
        rid = ms.build_report(user, run_id)
        return {"report_id": rid}


AGENTS = {a.key: a for a in (PriceWatcher(), Matcher(), MarketAnalyst())}


class AgentSupervisor:

    def __init__(self, ms, poll_seconds: int = 60):
        import threading
        self.ms = ms
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="borna-agents", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _loop(self) -> None:
        import logging
        log = logging.getLogger("borna.market")
        try:
            n = self.ms.recover_interrupted()
            if n:
                log.warning("%s interrupted agent run(s) marked as failed", n)
        except Exception:
            log.exception("could not recover interrupted agent runs")
        log.info("agent supervisor started (poll every %ss)", self.poll_seconds)
        while not self._stop.wait(self.poll_seconds):
            try:
                for r in self.ms.scheduled_tick():
                    log.info("agent %s: %s %s", r["agent"], r["status"], r.get("message") or "")
            except Exception:
                log.exception("agent supervisor tick failed")
        log.info("agent supervisor stopped")

    def stop(self) -> None:
        self._stop.set()
