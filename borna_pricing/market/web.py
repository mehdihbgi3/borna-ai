from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace

from flask import abort, flash, g, redirect, render_template, request, send_file, url_for

from .. import charts, fmt
from ..clock import now_utc, parse_iso
from ..service import ServiceError
from . import catalog, evidence, matching, prices, report, textnorm as tn
from .agents import LEVELS
from .service import (CHANNELS, OBS_STATUS, POLICY_LABELS, SOURCE_KINDS, STATE_LABELS)

INLINE = ("png", "jpg", "webp")


def globals_for_templates() -> dict:
    return {"TIER_LABELS": tn.TIER_LABELS, "LEVEL_LABELS": prices.LEVEL_LABELS, "UNIT_LABELS": prices.UNIT_LABELS,
            "AVAILABILITY_LABELS": prices.AVAILABILITY_LABELS, "PRICE_KINDS": prices.PRICE_KINDS,
            "OBS_STATUS": OBS_STATUS, "STATE_LABELS": STATE_LABELS, "POLICY_LABELS": POLICY_LABELS,
            "SOURCE_KINDS": SOURCE_KINDS, "CHANNELS": CHANNELS, "VERDICT_LABELS": matching.VERDICT_LABELS,
            "RELATION_LABELS": matching.RELATION_LABELS, "SPEC_ATTRS": catalog.SPEC_ATTRS, "AGENT_LEVELS": LEVELS,
            "attr_display": catalog.display, "VEHICLE_LABELS": tn.VEHICLE_LABELS}


def compare_rows(prof: dict | None, attrs: dict) -> list[dict]:
    prof = prof or {}
    rows = []
    for attr, label in (("part_nos", "پارت‌نامبر"), ("design", "طرح"), ("ecu", "ECU"), ("socket_color", "رنگ سوکت"),
                        ("wire_color", "رنگ سیم"), ("length_mm", "طول سیم"), ("length_class", "رده طول"),
                        ("wire_count", "تعداد سیم"), ("position", "موقعیت"), ("generation", "نسل"),
                        ("vehicles", "خودرو")):
        a = attrs.get(attr) if attr != "ecu" else (attrs.get("ecu") or attrs.get("ecus"))
        p = prof.get(attr)
        if a in (None, [], "") and p in (None, [], ""):
            continue
        shown_a = catalog.display("ecus" if attr == "ecu" and isinstance(a, list) else attr, a)
        same = None
        if a not in (None, [], "") and p not in (None, [], ""):
            if attr == "part_nos":
                same = bool(set(a) & set(p))
            elif attr == "vehicles":
                same = bool(set(a) & set(p))
            elif attr == "ecu" and isinstance(a, list):
                same = p in a
            else:
                same = a == p
        rows.append({"label": label, "listing": shown_a, "product": catalog.display(attr, p), "same": same})
    return rows


def register(app, svc, ms, h: SimpleNamespace) -> None:
    need, uname = h.need, h.uname

    def role():
        return g.user["role"]

    def done(msg, kind="ok"):
        flash(msg, kind)

    def back(default, **kw):
        nxt = request.form.get("next") or ""
        if nxt.startswith("/") and not nxt.startswith("//"):
            return redirect(nxt)
        return redirect(url_for(default, **kw))

    def guarded(fn, ok_msg, default, **kw):
        try:
            res = fn()
            done(ok_msg(res) if callable(ok_msg) else ok_msg)
        except ServiceError as exc:
            done(str(exc), "error")
        return back(default, **kw)

    @app.get("/market")
    @need()
    def market_overview():
        ov = ms.overview()
        with_data = [r for r in ov["table"] if r["has_data"]]
        show_all = request.args.get("all") == "1" or not with_data
        return render_template("market_overview.html", ov=ov, rows=ov["table"] if show_all else with_data,
                               show_all=show_all, n_data=len(with_data))

    @app.get("/market/products/<key>")
    @need()
    def market_product(key):
        d = ms.product_market(key)
        if d is None:
            abort(404)
        chart = charts.line_chart(d["series"], value_fmt=lambda v: fmt.money(round(v)),
                                  axis_fmt=charts.axis_rial_million, unit="ریال", step=True, extend_to=now_utc(),
                                  ref=(d["official"], f"قیمت رسمی برنا {fmt.money(d['official'])}") if d["official"] else None)
        return render_template("market_product.html", d=d, p=d["product"], chart=chart,
                               stale_before=now_utc() - timedelta(days=ms.cfg.market.stale_days))

    @app.post("/market/products/<key>/specs")
    @need("admin")
    def market_product_specs(key):
        f = request.form
        if f.get("action") == "remove":
            return guarded(lambda: ms.remove_spec(key, f.get("attr", ""), f.get("value", ""), uname()),
                           "ویژگی حذف شد و تطبیق‌ها دوباره بررسی شد.", "market_product", key=key)
        return guarded(lambda: ms.add_spec(key, f.get("attr", ""), f.get("value", ""), uname(), f.get("note", "")),
                       "ویژگی ثبت شد و تطبیق‌ها دوباره بررسی شد.", "market_product", key=key)

    @app.get("/market/review")
    @need("approver")
    def market_review():
        queue = ms.review_queue()
        for e in queue:
            for m in e["proposals"]:
                m["compare"] = compare_rows(m["profile"], e["attrs_d"])
        n_strong = sum(1 for e in queue if len(e["proposals"]) == 1 and e["proposals"][0]["verdict"] == "strong")
        return render_template("market_review.html", queue=queue, n_strong=n_strong)

    @app.post("/market/review/approve-strong")
    @need("approver")
    def market_approve_strong():
        return guarded(lambda: ms.approve_strong(uname()), lambda n: f"{fmt.fa(n)} تطبیق قوی تأیید شد.",
                       "market_review")

    @app.post("/market/matches/<int:match_id>/decide")
    @need("approver")
    def market_match_decide(match_id):
        accept = request.form.get("decision") == "accept"
        return guarded(lambda: ms.decide_match(match_id, accept, uname(), request.form.get("note", "")),
                       "تطبیق تأیید شد." if accept else "پیشنهاد رد شد.", "market_review")

    @app.post("/market/matches/<int:match_id>/unlink")
    @need("approver")
    def market_match_unlink(match_id):
        return guarded(lambda: ms.unlink(match_id, uname(), request.form.get("note", "")), "تطبیق لغو شد.",
                       "market_listings")

    @app.get("/market/listings")
    @need()
    def market_listings():
        state = request.args.get("state") or None
        sid = request.args.get("source", type=int)
        return render_template("market_listings.html", rows=ms.listings(state, sid), state=state, source_id=sid,
                               sources=ms.sources())

    @app.get("/market/listings/<int:listing_id>")
    @need()
    def market_listing(listing_id):
        d = ms.listing(listing_id)
        if d is None:
            abort(404)
        profs = {p["key"]: p for p in ms.profiles()}
        for m in d["matches"]:
            m["compare"] = compare_rows(profs.get(m["product_key"]), d["attrs_d"])
        return render_template("market_listing.html", e=d, products=svc.products(include_inactive=False))

    @app.post("/market/listings/<int:listing_id>/link")
    @need("approver")
    def market_listing_link(listing_id):
        return guarded(lambda: ms.manual_link(listing_id, request.form.get("product_key", ""), uname(),
                                              request.form.get("note", "")),
                       "آگهی به کالای برنا وصل شد.", "market_listing", listing_id=listing_id)

    @app.post("/market/listings/<int:listing_id>/tier")
    @need("approver")
    def market_listing_tier(listing_id):
        return guarded(lambda: ms.set_tier(listing_id, request.form.get("tier", ""), uname()), "اصالت آگهی ثبت شد.",
                       "market_listing", listing_id=listing_id)

    @app.post("/market/listings/<int:listing_id>/own")
    @need("approver")
    def market_listing_own(listing_id):
        own = request.form.get("own") == "1"
        return guarded(lambda: ms.set_own(listing_id, own, uname()),
                       "آگهی مال برنا علامت خورد." if own else "علامت آگهی برنا برداشته شد.",
                       "market_listing", listing_id=listing_id)

    @app.get("/market/observations")
    @need("approver")
    def market_observations():
        return render_template("market_observations.html", rows=ms.observations_to_review())

    @app.post("/market/observations/<int:obs_id>/decide")
    @need("approver")
    def market_observation_decide(obs_id):
        accept = request.form.get("decision") == "accept"
        return guarded(lambda: ms.decide_observation(obs_id, accept, uname(), request.form.get("note", "")),
                       "قیمت تأیید شد و در مقایسه‌ها حساب می‌شود." if accept else "قیمت رد شد.", "market_observations")

    @app.route("/market/manual", methods=["GET", "POST"])
    @need()
    def market_manual():
        form = {}
        if request.method == "POST":
            form = request.form.to_dict()
            f = request.files.get("evidence")
            upload = None
            if f and f.filename:
                data = f.read(ms.cfg.collector.max_upload_mb * 1024 * 1024 + 1)
                upload = (data, f.filename)
            try:
                oid = ms.manual_observation(form, uname(), role(), upload)
                if role() == "viewer":
                    done("قیمت رقیب ثبت شد و بعد از تأیید مالی یا مدیر در مقایسه‌ها حساب می‌شود.")
                else:
                    done(f"قیمت رقیب ثبت شد (شماره {fmt.fa(oid)}).")
                return redirect(url_for("market_manual"))
            except ServiceError as exc:
                done(str(exc), "error")
        return render_template("market_manual.html", form=form, products=svc.products(include_inactive=False),
                               max_mb=ms.cfg.collector.max_upload_mb)

    @app.get("/market/evidence/<int:snapshot_id>")
    @need()
    def market_evidence(snapshot_id):
        snap = ms.snapshot(snapshot_id)
        if snap is None:
            abort(404)
        try:
            data = ms.snapshot_bytes(snap)
        except ServiceError:
            abort(404)
        ext = evidence.EvidenceStore.ext_of(snap["path"])
        name = f"borna_evidence_{snapshot_id}"
        if ext in INLINE:
            return send_file(BytesIO(data), mimetype=evidence.MIME[ext], download_name=f"{name}.{ext}")
        if ext == "pdf":
            return send_file(BytesIO(data), mimetype="application/pdf", as_attachment=True, download_name=f"{name}.pdf")
        return send_file(BytesIO(data), mimetype="text/plain; charset=utf-8", as_attachment=True,
                         download_name=f"{name}.{ext}.txt")

    @app.get("/market/sources")
    @need("approver")
    def market_sources():
        return render_template("market_sources.html", sources=ms.sources())

    @app.route("/market/sources/new", methods=["GET", "POST"])
    @need("admin")
    def market_source_new():
        form = {"unit": "toman", "price_level": "retail", "parser": "auto", "interval_hours": 24,
                "delay_seconds": ms.cfg.collector.min_delay_seconds, "max_pages": 10, "active": "1", "kind": "marketplace"}
        if request.method == "POST":
            form = request.form.to_dict()
            try:
                sid = ms.save_source(form, uname())
                done("منبع ساخته شد. پیش از پایش، robots.txt و شرایط استفاده سایت را بررسی و مجوز را ثبت کنید.")
                return redirect(url_for("market_source", source_id=sid))
            except ServiceError as exc:
                done(str(exc), "error")
        return render_template("market_source.html", s=None, form=form, snaps=[], test=None)

    @app.route("/market/sources/<int:source_id>", methods=["GET", "POST"])
    @need("approver")
    def market_source(source_id):
        s = ms.get_source(source_id)
        if s is None or s["kind"] == "manual":
            abort(404)
        test = None
        form = dict(s, start_urls="\n".join(s["start_urls"]))
        if request.method == "POST":
            if not h.can("admin"):
                abort(403)
            action = request.form.get("action")
            if action == "test":
                try:
                    test = ms.test_parser(source_id, request.form.to_dict())
                except ServiceError as exc:
                    done(str(exc), "error")
                form.update(request.form.to_dict())
            else:
                form = request.form.to_dict()
                try:
                    ms.save_source(form, uname(), source_id)
                    done("تغییرات منبع ذخیره شد.")
                    return redirect(url_for("market_source", source_id=source_id))
                except ServiceError as exc:
                    done(str(exc), "error")
        return render_template("market_source.html", s=s, form=form, snaps=ms.snapshots(source_id), test=test)

    @app.post("/market/sources/<int:source_id>/policy")
    @need("admin")
    def market_source_policy(source_id):
        return guarded(lambda: ms.set_policy(source_id, request.form.get("status", ""), request.form.get("note", ""),
                                             uname()), "وضعیت مجوز پایش ثبت شد.", "market_source", source_id=source_id)

    @app.post("/market/sources/<int:source_id>/run")
    @need("approver")
    def market_source_run(source_id):
        def run():
            st = ms.collect_source(source_id, uname())
            m = ms.match_pending(uname())
            return st, m

        def msg(res):
            st, m = res
            text = (f"{fmt.fa(st['pages'])} صفحه خوانده شد، {fmt.fa(st['items'])} کالا، "
                    f"{fmt.fa(st['observations'])} قیمت تازه، {fmt.fa(st['quarantined'])} قرنطینه، "
                    f"{fmt.fa(m['proposed'])} پیشنهاد تطبیق.")
            if st["errors"]:
                text += " خطا: " + "؛ ".join(st["errors"])[:300]
            return text
        return guarded(run, msg, "market_source", source_id=source_id)

    @app.get("/market/agents")
    @need("approver")
    def market_agents():
        return render_template("market_agents.html", agents=ms.agents_overview(), runs=ms.agent_runs(60),
                               paused=ms.paused(), enabled=ms.cfg.agents.enabled)

    @app.post("/market/agents/<key>/run")
    @need("approver")
    def market_agent_run(key):
        def msg(r):
            return f"اجرا تمام شد: {r['status']}" + (f" — {r['message']}" if r.get("message") else "")
        return guarded(lambda: ms.run_agent(key, "manual", uname()), msg, "market_agents")

    @app.post("/market/agents/pause")
    @need("admin")
    def market_agents_pause():
        paused = request.form.get("paused") == "1"
        return guarded(lambda: ms.set_paused(paused, uname()),
                       "اجرای خودکار ایجنت‌ها متوقف شد." if paused else "اجرای خودکار ایجنت‌ها دوباره روشن شد.",
                       "market_agents")

    @app.get("/market/reports")
    @need()
    def market_reports():
        return render_template("market_reports.html", reports=ms.reports())

    @app.post("/market/reports/build")
    @need("approver")
    def market_report_build():
        try:
            rid = ms.build_report(uname())
            done("گزارش تازه ساخته شد.")
            return redirect(url_for("market_report", report_id=rid))
        except ServiceError as exc:
            done(str(exc), "error")
            return redirect(url_for("market_reports"))

    @app.get("/market/reports/<int:report_id>")
    @need()
    def market_report(report_id):
        rep = ms.report(report_id)
        if rep is None:
            abort(404)
        return render_template("market_report.html", rep=rep, data=rep["data"])

    @app.get("/market/reports/<int:report_id>.xlsx")
    @need()
    def market_report_xlsx(report_id):
        rep = ms.report(report_id)
        if rep is None:
            abort(404)
        stamp = parse_iso(rep["created_at"])
        return send_file(BytesIO(report.xlsx(rep, ms.cfg.web.company_name)), as_attachment=True,
                         download_name=f"Borna_Market_Report_{report_id}_{stamp:%Y%m%d}.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
