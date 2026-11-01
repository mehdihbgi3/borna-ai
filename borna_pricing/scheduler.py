from __future__ import annotations

import logging
import threading

from .clock import now_utc, tehran_date

log = logging.getLogger("borna")


class Scheduler(threading.Thread):
    def __init__(self, service, poll_seconds: int = 30):
        super().__init__(name="borna-scheduler", daemon=True)
        self.service = service
        self.poll_seconds = poll_seconds
        self._stop_event = threading.Event()
        self._last_backup_day = None

    def run(self) -> None:
        log.info("scheduler started (poll every %ss)", self.poll_seconds)
        while not self._stop_event.is_set():
            self.tick()
            self._stop_event.wait(self.poll_seconds)
        log.info("scheduler stopped")

    def tick(self) -> None:
        now = now_utc()
        try:
            day = tehran_date(now)
            if self._last_backup_day != day:
                path = self.service.backup()
                self._last_backup_day = day
                log.info("daily backup: %s", path)
        except Exception:
            log.exception("daily backup failed")
        try:
            res = self.service.scheduled_fetch(now)
            if res:
                log.info("scheduled fetch: %s", res.get("status"))
        except Exception:
            log.exception("scheduled fetch failed")

    def stop(self) -> None:
        self._stop_event.set()
