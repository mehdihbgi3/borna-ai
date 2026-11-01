from __future__ import annotations

import json
import logging
import re
import shutil
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

from . import db, engine, fmt, fx, importer
from .clock import TEHRAN, iso, now_utc, parse_iso, tehran_date, tehran_day_start
from .hours import BusinessHours

log = logging.getLogger("borna")

ROLES = ("viewer", "approver", "admin")
ROLE_LABELS = {"viewer": "فروش (فقط مشاهده)", "approver": "تأییدکننده (مالی / مدیرعامل)", "admin": "مدیر سامانه"}
RATE_STATUS = {
    "ok": "پذیرفته شد", "approved": "با تأیید پذیرفته شد", "held": "در انتظار تأیید",
    "rejected": "رد شد", "superseded": "جایگزین شد", "error": "خطا", "stale": "کهنه",
    "out_of_range": "خارج از بازه",
}
RATE_SOURCE = {"pricetoday": "priceto.day", "alanchand": "الان‌چند", "file": "فایل آزمون", "manual": "ورود دستی"}
PUB_KIND = {"base": "لیست پایه برنا", "normal": "انتشار عادی", "approved": "انتشار با تأیید", "auto": "انتشار خودکار"}
RUN_STATUS = {"draft": "پیش‌نویس — انتشار لازم است", "no_change": "بدون نیاز به انتشار",
              "published": "منتشر شد", "superseded": "جایگزین شد"}
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{3,32}$")
PW_METHOD = "pbkdf2:sha256:600000"


class ServiceError(Exception):
    pass


def _d(row) -> dict | None:
    return dict(row) if row is not None else None


class Service:
    def __init__(self, cfg):
        self.cfg = cfg
        self.hours = BusinessHours(cfg.hours)
        self.params = engine.PricingParams.from_config(cfg.pricing)
        self._lock = threading.RLock()

    @contextmanager
    def db(self):
        conn = db.connect(self.cfg.paths.database)
        try:
            yield conn
        finally:
            conn.close()

    def initialized(self) -> bool:
        if not Path(self.cfg.paths.database).exists():
            return False
        with self.db() as c:
            row = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='publications'").fetchone()
            return row is not None

    def ensure_schema(self) -> list[int]:
        with self._lock:
            with self.db() as c:
                if not db.pending_migrations(c):
                    return []
            self.backup(prefix=f"pre_v{db.SCHEMA_VERSION}", keep=0)
            with self.db() as c:
                done = db.migrate(c)
                if done:
                    db.audit(c, iso(now_utc()), "system", "schema_migrate", {"versions": done})
                return done

    def init(self, user: str = "system") -> dict:
        out = {"imported": None, "base_publication": None}
        with self._lock, self.db() as c:
            db.init_schema(c)
            n = c.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            if n == 0:
                out["imported"] = self._import(c, self.cfg.paths.products_xlsx, user, recompute=False)
            out["base_publication"] = self._ensure_base_publication(c, user)
        return out

    def _ensure_base_publication(self, c, user) -> int | None:
        row = c.execute("SELECT id FROM publications ORDER BY id LIMIT 1").fetchone()
        if row:
            return row["id"]
        prods = self._products(c)
        if not prods:
            return None
        p = self.cfg.pricing
        published = datetime.combine(p.base_date, p.base_time, tzinfo=TEHRAN)
        with db.tx(c):
            cur = c.execute(
                "INSERT INTO publications(published_at, kind, run_id, rate_id, rate_rial, published_by, note, valid_until)"
                " VALUES (?, 'base', NULL, NULL, ?, ?, ?, ?)",
                (iso(published), p.base_rate_rial, user, f"لیست قیمت برنا به تاریخ {p.base_date_text}",
                 iso(self.hours.validity_end(published))))
            pid = cur.lastrowid
            for pr in prods:
                c.execute("INSERT INTO published_prices VALUES (?,?,?,?,?,?,?,?,?)",
                          (pid, pr["key"], pr["row_no"], pr["name"], pr["part_no"], pr["vehicle"],
                           pr["length_text"], pr["moq"], pr["base_price_rial"]))
            db.audit(c, iso(now_utc()), user, "base_publication",
                     {"publication_id": pid, "date": p.base_date_text, "rate_toman": p.base_rate_toman,
                      "products": len(prods)})
        return pid

    def _products(self, c, include_inactive=False) -> list[dict]:
        sql = "SELECT * FROM products" + ("" if include_inactive else " WHERE active=1") + " ORDER BY row_no, key"
        return [dict(r) for r in c.execute(sql)]

    def products(self, include_inactive=True) -> list[dict]:
        with self.db() as c:
            return self._products(c, include_inactive)

    COMPARE = ("row_no", "status", "name", "catalog_name", "part_no", "design", "ecu", "vehicle",
               "length_text", "socket_color", "wire_color", "base_price_rial", "moq", "alpha", "note")
    IMPORTANT = ("name", "part_no", "base_price_rial", "alpha", "row_no")

    def _diff(self, c, items) -> dict:
        current = {p["key"]: p for p in self._products(c, include_inactive=True)}
        added, changed, reactivated = [], [], []
        new_keys = set()
        for it in items:
            new_keys.add(it["key"])
            old = current.get(it["key"])
            if old is None:
                added.append(it["key"])
                continue
            if not old["active"]:
                reactivated.append(it["key"])
            for f in self.COMPARE:
                if (old.get(f) or None) != (it.get(f) or None):
                    changed.append({"key": it["key"], "field": f, "old": old.get(f), "new": it.get(f),
                                    "important": f in self.IMPORTANT})
        removed = sorted(k for k, p in current.items() if p["active"] and k not in new_keys)
        return {"added": added, "changed": changed, "removed": removed, "reactivated": reactivated,
                "total": len(items)}

    def preview_import(self, path) -> dict:
        items, errors, warnings = importer.read_products(path, self.cfg.pricing.round_step_rial)
        with self.db() as c:
            diff = self._diff(c, items) if not errors else None
        return {"errors": errors, "warnings": warnings, "diff": diff, "count": len(items)}

    def import_products(self, path, user: str) -> dict:
        with self._lock, self.db() as c:
            return self._import(c, path, user, recompute=True)

    def _import(self, c, path, user, recompute) -> dict:
        try:
            items, errors, warnings = importer.read_products(path, self.cfg.pricing.round_step_rial)
        except importer.ImportErrorFa as exc:
            raise ServiceError(str(exc)) from None
        if errors:
            raise ServiceError("فایل کالاها خطا دارد؛ چیزی وارد نشد:\n" + "\n".join(errors[:25]))
        now = iso(now_utc())
        with db.tx(c):
            diff = self._diff(c, items)
            cols = ("key",) + self.COMPARE + ("part_no_norm", "length_mm")
            for it in items:
                vals = [it.get(k) for k in cols]
                c.execute(
                    f"INSERT INTO products({', '.join(cols)}, active, updated_at) VALUES ({', '.join('?' * len(cols))}, 1, ?)"
                    f" ON CONFLICT(key) DO UPDATE SET {', '.join(f'{k}=excluded.{k}' for k in cols[1:])},"
                    " active=1, updated_at=excluded.updated_at", vals + [now])
            for key in diff["removed"]:
                c.execute("UPDATE products SET active=0, updated_at=? WHERE key=?", (now, key))
            db.audit(c, now, user, "products_import",
                     {"file": Path(path).name, "total": len(items), "added": diff["added"],
                      "removed": diff["removed"], "reactivated": diff["reactivated"],
                      "changed": [f"{x['key']}.{x['field']}: {x['old']} → {x['new']}" for x in diff["changed"]]})
            if recompute and (diff["added"] or diff["changed"] or diff["removed"] or diff["reactivated"]):
                last = self._last_accepted(c)
                if last:
                    self._create_run(c, last["id"], now_utc(), user)
        diff["warnings"] = warnings
        return diff

    def _last_accepted(self, c):
        return c.execute("SELECT * FROM rates WHERE accepted=1 ORDER BY id DESC LIMIT 1").fetchone()

    def _pending_held(self, c):
        return c.execute("SELECT * FROM rates WHERE status='held' ORDER BY id DESC LIMIT 1").fetchone()

    def _fetch_allowed_at(self, c, now: datetime) -> datetime:
        g = self.cfg.guards
        t = now
        last_reached = c.execute("SELECT fetched_at FROM rates WHERE source <> 'manual' AND reached=1"
                                 " ORDER BY id DESC LIMIT 1").fetchone()
        if last_reached:
            t = max(t, parse_iso(last_reached["fetched_at"]) + timedelta(minutes=g.fetch_interval_minutes))
        last_any = c.execute("SELECT fetched_at, reached FROM rates WHERE source <> 'manual'"
                             " ORDER BY id DESC LIMIT 1").fetchone()
        if last_any and not last_any["reached"]:
            t = max(t, parse_iso(last_any["fetched_at"]) + timedelta(minutes=g.retry_minutes))
        return t

    def source_age_minutes(self, updated: datetime, now: datetime) -> float:
        return self.hours.open_minutes_between(updated, now)

    def next_auto_fetch_at(self, now: datetime | None = None) -> datetime | None:
        if self.cfg.source.mode == "off":
            return None
        now = now or now_utc()
        with self.db() as c:
            allowed = self._fetch_allowed_at(c, now)
        return self.hours.next_open(allowed)

    def fetch_now(self, user: str = "system", force: bool = False) -> dict:
        with self._lock:
            with self.db() as c:
                now = now_utc()
                allowed = self._fetch_allowed_at(c, now)
                if not force and allowed > now:
                    raise ServiceError(f"فاصله دو دریافت نرخ دست‌کم {fmt.fa(self.cfg.guards.fetch_interval_minutes)} "
                                       f"دقیقه است؛ دریافت بعدی از ساعت {fmt.hm(allowed)} ممکن است. "
                                       "اگر عجله دارید نرخ را دستی وارد کنید.")
            res = fx.fetch(self.cfg)
            with self.db() as c:
                return self._record_fetch(c, res, user)

    def scheduled_fetch(self, now: datetime | None = None) -> dict | None:
        with self._lock:
            now = now or now_utc()
            nxt = self.next_auto_fetch_at(now)
            if nxt is None or nxt > now:
                return None
            return self.fetch_now("system", force=True)

    def _record_fetch(self, c, res: fx.FetchResult, user: str) -> dict:
        now = now_utc()
        source = "file" if self.cfg.source.mode == "file" else self.cfg.source.provider
        slug, field = fx.series(self.cfg)
        with db.tx(c):
            last = self._last_accepted(c)
            if last:
                ref, label = last["value_toman"], "آخرین نرخ پذیرفته‌شده"
            else:
                ref, label = self.cfg.pricing.base_rate_toman, "نرخ مبنا"
            d = fx.evaluate(res, ref, label, now, self.cfg.guards, self.hours)
            if d.status in ("ok", "held"):
                c.execute("UPDATE rates SET status='superseded' WHERE status='held'")
            cur = c.execute(
                "INSERT INTO rates(fetched_at, source, slug, field, value_toman, source_updated_raw, source_updated_at,"
                " status, accepted, jump, message, raw, http_status, reached, created_by)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (iso(now), source, slug, field, res.value_toman,
                 res.source_updated_raw, iso(res.source_updated_utc) if res.source_updated_utc else None,
                 d.status, int(d.accepted), str(d.jump) if d.jump is not None else None, d.message,
                 res.raw_entry, res.http_status, int(res.reached), user))
            rid = cur.lastrowid
            db.audit(c, iso(now), user, "rate_fetch",
                     {"rate_id": rid, "status": d.status, "value_toman": res.value_toman, "message": d.message})
            run_id = self._create_run(c, rid, now, user) if d.accepted else None
        level = logging.INFO if d.status in ("ok", "held") else logging.WARNING
        log.log(level, "rate fetch #%s: status=%s value=%s %s", rid, d.status, res.value_toman, d.message)
        return {"rate_id": rid, "status": d.status, "message": d.message, "value_toman": res.value_toman,
                "run_id": run_id}

    def decide_held(self, rate_id: int, user: str, accept: bool, note: str = "") -> dict:
        with self._lock, self.db() as c, db.tx(c):
            r = c.execute("SELECT * FROM rates WHERE id=?", (rate_id,)).fetchone()
            if r is None or r["status"] != "held":
                raise ServiceError("این نرخ دیگر در انتظار تأیید نیست؛ صفحه را تازه کنید.")
            now = now_utc()
            if accept:
                if r["source_updated_at"]:
                    age = self.source_age_minutes(parse_iso(r["source_updated_at"]), now)
                    if age > self.cfg.guards.max_age_minutes:
                        raise ServiceError("این نرخ کهنه شده است؛ منتظر دریافت بعدی بمانید یا نرخ را دستی وارد کنید.")
                c.execute("UPDATE rates SET status='approved', accepted=1, decided_by=?, decided_at=? WHERE id=?",
                          (user, iso(now), rate_id))
                db.audit(c, iso(now), user, "rate_approve", {"rate_id": rate_id, "value_toman": r["value_toman"],
                                                             "note": note})
                return {"run_id": self._create_run(c, rate_id, now, user)}
            c.execute("UPDATE rates SET status='rejected', decided_by=?, decided_at=? WHERE id=?",
                      (user, iso(now), rate_id))
            db.audit(c, iso(now), user, "rate_reject", {"rate_id": rate_id, "value_toman": r["value_toman"],
                                                        "note": note})
            return {"run_id": None}

    def manual_rate(self, value_toman: int, user: str, note: str = "") -> dict:
        g = self.cfg.guards
        try:
            v = int(value_toman)
        except (TypeError, ValueError):
            raise ServiceError("نرخ باید عدد صحیح به تومان باشد.") from None
        if not g.min_rate_toman <= v <= g.max_rate_toman:
            raise ServiceError(f"نرخ باید بین {fmt.money(g.min_rate_toman)} و {fmt.money(g.max_rate_toman)} تومان باشد. "
                               "(به تومان وارد کنید، نه ریال)")
        if not note.strip():
            raise ServiceError("برای نرخ دستی، منبع یا دلیل را در توضیح بنویسید.")
        with self._lock, self.db() as c, db.tx(c):
            now = now_utc()
            last = self._last_accepted(c)
            ref = last["value_toman"] if last else self.cfg.pricing.base_rate_toman
            jump = Decimal(v) / Decimal(ref) - 1
            msg = f"ورود دستی: {note.strip()} ({fmt.pct_words(jump)} از {fmt.money(ref)} تومان)"
            c.execute("UPDATE rates SET status='superseded' WHERE status='held'")
            cur = c.execute(
                "INSERT INTO rates(fetched_at, source, value_toman, source_updated_at, status, accepted, jump, message,"
                " reached, created_by, decided_by, decided_at) VALUES (?, 'manual', ?, ?, 'ok', 1, ?, ?, 0, ?, ?, ?)",
                (iso(now), v, iso(now), str(jump), msg, user, user, iso(now)))
            rid = cur.lastrowid
            db.audit(c, iso(now), user, "rate_manual", {"rate_id": rid, "value_toman": v, "note": note.strip()})
            return {"rate_id": rid, "run_id": self._create_run(c, rid, now, user)}

    def _current_publication(self, c):
        return c.execute("SELECT * FROM publications ORDER BY id DESC LIMIT 1").fetchone()

    def _publication_before(self, c, t: datetime):
        return c.execute("SELECT * FROM publications WHERE published_at < ? ORDER BY published_at DESC, id DESC"
                         " LIMIT 1", (iso(t),)).fetchone()

    def _prices(self, c, pub_id) -> dict[str, int]:
        return {r["product_key"]: r["price_rial"] for r in
                c.execute("SELECT product_key, price_rial FROM published_prices WHERE publication_id=?", (pub_id,))}

    def _validity(self, c, pub) -> datetime:
        row = c.execute("SELECT MAX(valid_until) AS v FROM renewals WHERE publication_id=?", (pub["id"],)).fetchone()
        vals = [parse_iso(pub["valid_until"])] + ([parse_iso(row["v"])] if row and row["v"] else [])
        return max(vals)

    def _create_run(self, c, rate_id: int, now: datetime, user: str) -> int:
        rate = c.execute("SELECT * FROM rates WHERE id=?", (rate_id,)).fetchone()
        rate_rial = int(rate["value_toman"]) * 10
        prods = self._products(c)
        inputs = [engine.ProductInput(p["key"], p["base_price_rial"], Decimal(p["alpha"]) if p["alpha"] else None)
                  for p in prods]
        pub = self._current_publication(c)
        day = self._publication_before(c, tehran_day_start(now))
        pub_ref = engine.RefList(pub["rate_rial"], self._prices(c, pub["id"])) if pub else None
        day_ref = engine.RefList(day["rate_rial"], self._prices(c, day["id"])) if day else None
        res = engine.compute_run(inputs, rate_rial, self.params, pub_ref, day_ref)
        p = self.params
        params = {"base_rate_rial": p.base_rate_rial, "alpha_default": str(p.alpha_default),
                  "round_step_rial": p.round_step_rial, "publish_threshold": str(p.publish_threshold),
                  "approval_threshold": str(p.approval_threshold), "change_basis": p.change_basis}
        with db.tx(c):
            c.execute("UPDATE runs SET status='superseded' WHERE status='draft'")
            cur = c.execute(
                "INSERT INTO runs(created_at, rate_id, rate_rial, base_rate_rial, ref_publication_id,"
                " day_ref_publication_id, needs_publish, needs_approval, n_publish, n_approval, removed_keys, status,"
                " params, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (iso(now), rate_id, rate_rial, p.base_rate_rial, pub["id"] if pub else None,
                 day["id"] if day else None, int(res.needs_publish), int(res.needs_approval), res.n_publish,
                 res.n_approval, json.dumps(res.removed_keys), "draft" if res.needs_publish else "no_change",
                 json.dumps(params), user))
            run_id = cur.lastrowid
            c.executemany(
                "INSERT INTO run_prices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(run_id, r.key, r.base_price_rial, str(r.alpha), str(r.exact_rial.quantize(Decimal("0.01"))),
                  r.price_rial, r.pub_price_rial, _s(r.change_vs_pub), _s(r.fx_effect_vs_pub), r.day_price_rial,
                  _s(r.change_vs_day), _s(r.fx_effect_vs_day), int(r.flag_publish), int(r.flag_approval))
                 for r in res.rows])
            db.audit(c, iso(now), user, "run", {"run_id": run_id, "rate_id": rate_id, "rate_toman": rate_rial // 10,
                                                "needs_publish": res.needs_publish,
                                                "needs_approval": res.needs_approval,
                                                "n_publish": res.n_publish, "n_approval": res.n_approval})
            if res.needs_publish:
                if self.cfg.pricing.auto_publish and not res.needs_approval:
                    self._publish(c, run_id, "system", "auto", now, "انتشار خودکار (بدون نیاز به تأیید)")
            elif self.cfg.pricing.auto_renew:
                self._renew(c, run_id, rate_id, now, "system")
        return run_id

    def recompute(self, user: str) -> int:
        with self._lock, self.db() as c, db.tx(c):
            last = self._last_accepted(c)
            if not last:
                raise ServiceError("هنوز هیچ نرخ پذیرفته‌شده‌ای نیست؛ اول نرخ بگیرید یا دستی وارد کنید.")
            return self._create_run(c, last["id"], now_utc(), user)

    def publish(self, run_id: int, user: str, confirm_approval: bool = False, note: str = "") -> int:
        with self._lock, self.db() as c, db.tx(c):
            run = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            latest = c.execute("SELECT MAX(id) AS m FROM runs").fetchone()["m"]
            if run is None:
                raise ServiceError("محاسبه پیدا نشد.")
            if run["id"] != latest or run["status"] == "superseded":
                raise ServiceError("محاسبه جدیدتری انجام شده است؛ صفحه را تازه کنید و دوباره بررسی کنید.")
            if run["status"] != "draft":
                raise ServiceError(f"این محاسبه قابل انتشار نیست ({RUN_STATUS.get(run['status'], run['status'])}).")
            cur = self._current_publication(c)
            if (cur["id"] if cur else None) != run["ref_publication_id"]:
                raise ServiceError("پس از این محاسبه، لیست دیگری منتشر شده است؛ «محاسبه دوباره» را بزنید.")
            if run["needs_approval"] and not confirm_approval:
                raise ServiceError("تغییر بیش از سقف روزانه است؛ برای انتشار، تأیید را تیک بزنید.")
            kind = "approved" if run["needs_approval"] else "normal"
            return self._publish(c, run_id, user, kind, now_utc(), note.strip())

    def _publish(self, c, run_id, user, kind, now, note="") -> int:
        run = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        valid_until = self.hours.validity_end(now)
        with db.tx(c):
            cur = c.execute(
                "INSERT INTO publications(published_at, kind, run_id, rate_id, rate_rial, published_by, approved_by,"
                " note, valid_until) VALUES (?,?,?,?,?,?,?,?,?)",
                (iso(now), kind, run_id, run["rate_id"], run["rate_rial"], user,
                 user if kind == "approved" else None, note or None, iso(valid_until)))
            pid = cur.lastrowid
            c.execute(
                "INSERT INTO published_prices SELECT ?, rp.product_key, p.row_no, p.name, p.part_no, p.vehicle,"
                " p.length_text, p.moq, rp.price_rial FROM run_prices rp JOIN products p ON p.key = rp.product_key"
                " WHERE rp.run_id=?", (pid, run_id))
            c.execute("UPDATE runs SET status='published' WHERE id=?", (run_id,))
            db.audit(c, iso(now), user, "publish",
                     {"publication_id": pid, "run_id": run_id, "kind": kind, "rate_toman": run["rate_rial"] // 10,
                      "valid_until": iso(valid_until), "note": note})
        log.info("published list #%s (run #%s, %s) by %s", pid, run_id, kind, user)
        return pid

    def _renew(self, c, run_id, rate_id, now, user) -> bool:
        if not self.hours.is_open(now):
            return False
        pub = self._current_publication(c)
        if pub is None:
            return False
        end = self.hours.close_today(now)
        if end is None or self._validity(c, pub) >= end:
            return False
        with db.tx(c):
            c.execute("INSERT INTO renewals(publication_id, run_id, rate_id, valid_until, created_at, created_by)"
                      " VALUES (?,?,?,?,?,?)", (pub["id"], run_id, rate_id, iso(end), iso(now), user))
            db.audit(c, iso(now), user, "renew", {"publication_id": pub["id"], "run_id": run_id,
                                                  "valid_until": iso(end)})
        return True

    def renew(self, user: str) -> None:
        with self._lock, self.db() as c, db.tx(c):
            now = now_utc()
            run = c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
            if run is None or run["status"] != "no_change":
                raise ServiceError("تمدید فقط وقتی ممکن است که آخرین محاسبه نیاز به انتشار نداشته باشد.")
            if tehran_date(parse_iso(run["created_at"])) != tehran_date(now):
                raise ServiceError("آخرین محاسبه مال امروز نیست؛ اول نرخ امروز را بگیرید.")
            if not self.hours.is_open(now):
                raise ServiceError("تمدید فقط در ساعت کاری ممکن است.")
            if not self._renew(c, run["id"], run["rate_id"], now, user):
                raise ServiceError("لیست فعلی همین حالا تا پایان روز کاری معتبر است.")

    def state(self) -> dict:
        now = now_utc()
        with self.db() as c:
            last_acc = _d(self._last_accepted(c))
            last_row = _d(c.execute("SELECT * FROM rates ORDER BY id DESC LIMIT 1").fetchone())
            held = _d(self._pending_held(c))
            pub = _d(self._current_publication(c))
            run = _d(c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone())
            allowed = self._fetch_allowed_at(c, now)
            out = {"now": now, "last_accepted": last_acc, "last_attempt": last_row, "held": held,
                   "publication": pub, "run": run, "fetch_allowed_at": allowed,
                   "next_auto_fetch": (self.hours.next_open(allowed) if self.cfg.source.mode != "off" else None),
                   "is_open": self.hours.is_open(now)}
            if pub:
                vu = self._validity(c, pub)
                pub["valid_until_eff"] = vu
                pub["is_valid"] = now < vu
                pub["renewed"] = c.execute("SELECT * FROM renewals WHERE publication_id=? ORDER BY id DESC LIMIT 1",
                                           (pub["id"],)).fetchone() is not None
                pub["rows"] = [dict(r) for r in c.execute(
                    "SELECT * FROM published_prices WHERE publication_id=? ORDER BY row_no, product_key", (pub["id"],))]
                if pub["rate_id"]:
                    pub["rate"] = _d(c.execute("SELECT * FROM rates WHERE id=?", (pub["rate_id"],)).fetchone())
            if run:
                run["rate"] = _d(c.execute("SELECT * FROM rates WHERE id=?", (run["rate_id"],)).fetchone())
                run["removed"] = json.loads(run["removed_keys"] or "[]")
                run["rows"] = [dict(r) for r in c.execute(
                    "SELECT rp.*, p.row_no, p.name, p.part_no, p.vehicle, p.status AS product_status, p.length_text,"
                    " p.moq"
                    " FROM run_prices rp JOIN products p ON p.key = rp.product_key WHERE rp.run_id=?"
                    " ORDER BY p.row_no, p.key", (run["id"],))]
                run["is_current"] = bool(pub) and run["ref_publication_id"] == pub["id"]
            if last_acc:
                out["change_vs_published"] = (Decimal(last_acc["value_toman"] * 10) / Decimal(pub["rate_rial"]) - 1
                                              if pub else None)
                out["change_vs_base"] = (Decimal(last_acc["value_toman"]) / Decimal(self.cfg.pricing.base_rate_toman)
                                         - 1)
        return out

    def publication(self, pub_id: int | None = None) -> dict | None:
        with self.db() as c:
            row = (c.execute("SELECT * FROM publications WHERE id=?", (pub_id,)).fetchone() if pub_id
                   else self._current_publication(c))
            if row is None:
                return None
            pub = dict(row)
            pub["valid_until_eff"] = self._validity(c, row)
            pub["is_valid"] = now_utc() < pub["valid_until_eff"]
            pub["is_current"] = self._current_publication(c)["id"] == pub["id"]
            pub["rows"] = [dict(r) for r in c.execute(
                "SELECT * FROM published_prices WHERE publication_id=? ORDER BY row_no, product_key", (pub["id"],))]
            if pub["rate_id"]:
                pub["rate"] = _d(c.execute("SELECT * FROM rates WHERE id=?", (pub["rate_id"],)).fetchone())
            return pub

    def publications(self, limit: int = 200) -> list[dict]:
        with self.db() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT p.*, (SELECT COUNT(*) FROM published_prices x WHERE x.publication_id=p.id) AS n,"
                " (SELECT MAX(valid_until) FROM renewals r WHERE r.publication_id=p.id) AS renewed_until,"
                " (SELECT COUNT(*) FROM renewals r WHERE r.publication_id=p.id) AS n_renewals"
                " FROM publications p ORDER BY p.id DESC LIMIT ?", (limit,))]
        return rows

    def rate_log(self, limit: int = 300) -> list[dict]:
        with self.db() as c:
            return [dict(r) for r in c.execute("SELECT * FROM rates ORDER BY id DESC LIMIT ?", (limit,))]

    def accepted_rates(self, days: int = 30) -> list[dict]:
        since = iso(now_utc() - timedelta(days=days))
        with self.db() as c:
            return [dict(r) for r in c.execute(
                "SELECT id, fetched_at, value_toman, source FROM rates WHERE accepted=1 AND fetched_at >= ?"
                " ORDER BY fetched_at", (since,))]

    def product_history(self, key: str) -> dict | None:
        with self.db() as c:
            prod = c.execute("SELECT * FROM products WHERE key=?", (key,)).fetchone()
            if prod is None:
                return None
            hist = [dict(r) for r in c.execute(
                "SELECT p.id, p.published_at, p.kind, p.rate_rial, p.published_by, pp.price_rial"
                " FROM published_prices pp JOIN publications p ON p.id = pp.publication_id"
                " WHERE pp.product_key=? ORDER BY p.id", (key,))]
            last_run = c.execute(
                "SELECT rp.*, r.created_at, r.status AS run_status FROM run_prices rp JOIN runs r ON r.id = rp.run_id"
                " WHERE rp.product_key=? ORDER BY r.id DESC LIMIT 1", (key,)).fetchone()
            return {"product": dict(prod), "history": hist, "last_run": _d(last_run)}

    def audit_log(self, limit: int = 500, action: str | None = None) -> list[dict]:
        with self.db() as c:
            if action:
                rows = c.execute("SELECT * FROM audit WHERE action=? ORDER BY id DESC LIMIT ?", (action, limit))
            else:
                rows = c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["details_obj"] = json.loads(d["details"] or "{}")
                except ValueError:
                    d["details_obj"] = {}
                out.append(d)
            return out

    def audit_actions(self) -> list[str]:
        with self.db() as c:
            return [r[0] for r in c.execute("SELECT DISTINCT action FROM audit ORDER BY action")]

    def users(self) -> list[dict]:
        with self.db() as c:
            return [dict(r) for r in c.execute("SELECT id, username, full_name, role, active, created_at, last_login"
                                               " FROM users ORDER BY active DESC, role DESC, username")]

    def user_count(self) -> int:
        with self.db() as c:
            return c.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def get_user(self, user_id: int) -> dict | None:
        with self.db() as c:
            return _d(c.execute("SELECT id, username, full_name, role, active FROM users WHERE id=?",
                                (user_id,)).fetchone())

    @staticmethod
    def _check_password(pw: str):
        if len(pw or "") < 8:
            raise ServiceError("رمز عبور باید دست‌کم ۸ نویسه باشد.")

    def create_user(self, username: str, password: str, role: str, full_name: str, by: str) -> int:
        username = (username or "").strip()
        if not USERNAME_RE.match(username):
            raise ServiceError("نام کاربری باید ۳ تا ۳۲ نویسه از حروف انگلیسی، عدد، نقطه، خط تیره یا _ باشد.")
        if role not in ROLES:
            raise ServiceError("نقش نامعتبر است.")
        self._check_password(password)
        with self._lock, self.db() as c, db.tx(c):
            if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise ServiceError(f"کاربر «{username}» از قبل وجود دارد.")
            cur = c.execute("INSERT INTO users(username, full_name, role, pw_hash, created_at) VALUES (?,?,?,?,?)",
                            (username, (full_name or "").strip() or None, role,
                             generate_password_hash(password, method=PW_METHOD), iso(now_utc())))
            db.audit(c, iso(now_utc()), by, "user_create", {"username": username, "role": role})
            return cur.lastrowid

    def verify_login(self, username: str, password: str) -> dict | None:
        with self.db() as c, db.tx(c):
            row = c.execute("SELECT * FROM users WHERE username=? AND active=1", ((username or "").strip(),)).fetchone()
            now = iso(now_utc())
            if row and check_password_hash(row["pw_hash"], password or ""):
                c.execute("UPDATE users SET last_login=? WHERE id=?", (now, row["id"]))
                db.audit(c, now, row["username"], "login", {})
                return {k: row[k] for k in ("id", "username", "full_name", "role")}
            db.audit(c, now, (username or "")[:40], "login_failed", {})
            return None

    def check_password(self, username: str, password: str) -> bool:
        with self.db() as c:
            row = c.execute("SELECT pw_hash FROM users WHERE username=? AND active=1", (username,)).fetchone()
            return bool(row) and check_password_hash(row["pw_hash"], password or "")

    def _admins_left(self, c, excluding_id: int) -> int:
        return c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1 AND id<>?",
                         (excluding_id,)).fetchone()[0]

    def update_user(self, user_id: int, by: str, role: str | None = None, active: bool | None = None,
                    password: str | None = None, full_name: str | None = None) -> None:
        with self._lock, self.db() as c, db.tx(c):
            u = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if u is None:
                raise ServiceError("کاربر پیدا نشد.")
            changes = {}
            if role is not None and role != u["role"]:
                if role not in ROLES:
                    raise ServiceError("نقش نامعتبر است.")
                if u["role"] == "admin" and self._admins_left(c, user_id) == 0:
                    raise ServiceError("دست‌کم یک مدیر سامانه فعال باید بماند.")
                c.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
                changes["role"] = f"{u['role']} → {role}"
            if active is not None and bool(active) != bool(u["active"]):
                if not active and u["role"] == "admin" and self._admins_left(c, user_id) == 0:
                    raise ServiceError("دست‌کم یک مدیر سامانه فعال باید بماند.")
                c.execute("UPDATE users SET active=? WHERE id=?", (int(bool(active)), user_id))
                changes["active"] = bool(active)
            if password:
                self._check_password(password)
                c.execute("UPDATE users SET pw_hash=? WHERE id=?",
                          (generate_password_hash(password, method=PW_METHOD), user_id))
                changes["password"] = "changed"
            if full_name is not None and full_name.strip() != (u["full_name"] or ""):
                c.execute("UPDATE users SET full_name=? WHERE id=?", (full_name.strip() or None, user_id))
                changes["full_name"] = full_name.strip()
            if changes:
                db.audit(c, iso(now_utc()), by, "user_update", {"username": u["username"], **changes})

    def user_id_by_name(self, username: str) -> int | None:
        with self.db() as c:
            row = c.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            return row["id"] if row else None

    def backup(self, dest_dir: Path | None = None, keep: int = 30, prefix: str = "borna_pricing") -> Path:
        import sqlite3
        dest_dir = Path(dest_dir or Path(self.cfg.paths.database).parent / "backups")
        dest_dir.mkdir(parents=True, exist_ok=True)
        stamp = now_utc().astimezone(TEHRAN)
        target = dest_dir / f"{prefix}_{stamp:%Y%m%d_%H%M%S}.sqlite3"
        with self.db() as c:
            dst = sqlite3.connect(str(target))
            try:
                c.backup(dst)
            finally:
                dst.close()
        olds = sorted(dest_dir.glob(f"{prefix}_*.sqlite3"))
        for old in olds[:-keep] if keep else []:
            try:
                old.unlink()
            except OSError:
                pass
        return target


def _s(x) -> str | None:
    return None if x is None else str(x.quantize(Decimal("0.000001")))


def copy_example_settings(root: Path) -> Path | None:
    target, example = root / "settings.ini", root / "settings.example.ini"
    if target.exists() or not example.exists():
        return None
    shutil.copyfile(example, target)
    return target
