"""The Machine Locator web app.

Everything the CLI can do is a button here: scanning for prospects, working the
pipeline, drafting and sending outreach, hunting routes for sale, planning a
day's driving. The CLI still exists and shares the same database -- this is a
second front door, not a replacement.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from flask import Flask, jsonify, render_template, request, send_file, session

import json

from ..config import SEED_SETTINGS, Settings
from ..db import PIPELINE_STAGES, STAGE_KEYS, Database
from ..export import (
    listings_to_csv, listings_to_json, sites_to_csv, sites_to_geojson,
)
from ..jobs import JobBusy, JobRunner
from ..locations.categories import CATEGORY_SPECS
from ..locations.pipeline import assign_territories, find_locations, plan_service_route
from ..locations.scoring import machine_recommendation
from ..models import utcnow
from ..outreach import sequences as seq
from ..outreach.compliance import SenderIdentity, check_send_gate, recipient_problem
from ..outreach.inbox import ImapConfig, check_replies, fetch_raw_messages, InboxError
from ..outreach.sender import SmtpConfig, test_connection
from ..outreach.templates import (
    MERGE_FIELDS, build_context, install_builtins, render, sequence_steps,
)
from . import auth

STAGE_LABELS = dict(PIPELINE_STAGES)


def _loads_setting(database, key):
    """A JSON setting, or an empty list when it has never been written."""
    try:
        return json.loads(database.get_setting(key, "") or "[]")
    except ValueError:
        return []


def create_app(settings: Optional[Settings] = None, public: bool = False) -> Flask:
    """Build the app. ``public`` means it is reachable from outside this
    machine, which makes a password mandatory rather than optional."""
    settings = settings or Settings()
    settings.ensure_dirs()
    app = Flask(__name__)
    app.config["SETTINGS"] = settings
    # Key order carries meaning in several payloads (pipeline stages are a
    # funnel, not an alphabet), so don't let the JSON encoder re-sort them.
    app.json.sort_keys = False
    runner = JobRunner(settings)

    # Seed the built-in templates and starting settings once, so a fresh
    # install opens already configured rather than blank.
    with Database(settings.db_path) as database:
        install_builtins(database)
        for key, value in SEED_SETTINGS.items():
            if not database.get_setting(key, ""):
                database.set_setting(key, value)

    def db() -> Database:
        return Database(settings.db_path)

    # One password, one operator. Whether it asks is decided per request, so a
    # password set from Settings takes effect without a restart.
    auth.install(app, db, public=public)

    def identity_and_smtp(database: Database):
        stored = database.all_settings()
        stored.setdefault("city", settings.city)
        return SenderIdentity.from_settings(stored), SmtpConfig.from_settings(stored)

    def imap_config(database: Database) -> ImapConfig:
        return ImapConfig.from_settings(database.all_settings())

    def shell(database: Database) -> Dict[str, Any]:
        """Context every page needs for the chrome."""
        identity, smtp = identity_and_smtp(database)
        return {
            "city": settings.city,
            "stats": database.stats(),
            "active_job": database.active_job(),
            "setup_complete": identity.is_complete and smtp.is_configured,
            "stages": PIPELINE_STAGES,
            "auth_enabled": auth.has_password(database),
        }

    # ------------------------------------------------------------------ pages

    @app.route("/")
    def index() -> str:
        with db() as database:
            return render_template("dashboard.html", **shell(database))

    @app.route("/prospects")
    def prospects_page() -> str:
        with db() as database:
            return render_template(
                "prospects.html",
                categories=sorted(
                    [{"key": s.key, "label": s.label} for s in CATEGORY_SPECS],
                    key=lambda c: c["label"],
                ),
                center=settings.center,
                **shell(database),
            )

    @app.route("/pipeline")
    def pipeline_page() -> str:
        with db() as database:
            return render_template("pipeline.html", **shell(database))

    @app.route("/outreach")
    def outreach_page() -> str:
        with db() as database:
            return render_template(
                "outreach.html", merge_fields=MERGE_FIELDS, **shell(database)
            )

    @app.route("/listings")
    def listings_page() -> str:
        with db() as database:
            return render_template("listings.html", **shell(database))

    @app.route("/planner")
    def planner_page() -> str:
        with db() as database:
            return render_template("planner.html", center=settings.center, **shell(database))

    @app.route("/settings")
    def settings_page() -> str:
        with db() as database:
            stored = database.all_settings()
            identity, smtp = identity_and_smtp(database)
            gate = check_send_gate(database, identity, smtp.is_configured)
            return render_template(
                "settings.html",
                stored=stored,
                missing=identity.missing_fields(),
                smtp_configured=smtp.is_configured,
                imap_configured=imap_config(database).is_configured,
                has_password=auth.has_password(database),
                password_from_env=auth.password_is_from_env(),
                is_public=app.config.get("PUBLIC_INSTANCE", False),
                gate=gate,
                suppression=database.suppression_list(),
                **shell(database),
            )

    # ------------------------------------------------------------------ stats

    @app.route("/api/stats")
    def api_stats() -> Any:
        with db() as database:
            grades = database.grade_counts()
            pipeline = database.pipeline_counts()
            # An ordered list, not a dict: these are funnel stages and the
            # order is part of the meaning.
            stages = [{"key": key, "label": label, "count": pipeline.get(key, 0)}
                      for key, label in PIPELINE_STAGES]
            # "Best" means highest average score, not most numerous -- a metro
            # has plenty of government offices and they are hard accounts to
            # win. Categories with only a couple of examples are excluded so a
            # single lucky site cannot top the chart.
            categories = sorted(
                [c for c in database.site_categories() if c["n"] >= 3],
                key=lambda c: -(c["avg_score"] or 0),
            )[:8]
            return jsonify({
                "stats": database.stats(),
                "grades": grades,
                "pipeline": pipeline,
                "stages": stages,
                "outreach": database.outreach_stats(),
                "outreach_daily": database.outreach_daily_counts(30),
                "categories": categories,
                "activities": database.recent_activities(12),
                "due_today": len(database.query_messages(status="queued", due_only=True, limit=500)),
            })

    @app.route("/api/today")
    def api_today() -> Any:
        """Everything that needs a human today, in one call."""
        with db() as database:
            identity, smtp = identity_and_smtp(database)
            gate = check_send_gate(database, identity, smtp.is_configured)
            due_mail = database.query_messages(status="queued", due_only=True, limit=200)
            # How many good prospects cannot be emailed only because nobody
            # has an address yet -- the step this app can do for you.
            missing = database.conn.execute(
                "SELECT COUNT(*) c FROM sites s "
                "LEFT JOIN pipeline p ON p.site_id = s.id "
                "WHERE s.score >= 65 AND (s.email IS NULL OR s.email = '') "
                "  AND (p.contact_email IS NULL OR p.contact_email = '')"
            ).fetchone()["c"]
            return jsonify({
                "due_emails": len(due_mail),
                "actions": database.due_actions(),
                "replies": database.awaiting_reply_followup(),
                "failed": database.outreach_stats().get("failed", 0),
                "missing_contacts": missing,
                "can_send": gate.allowed,
                "send_blocked_because": gate.reasons,
            })

    # ------------------------------------------------------------------- jobs

    def _start(kind: str, fn, message: str) -> Any:
        with db() as database:
            try:
                handle = runner.start(database, kind, fn, message)
            except JobBusy as exc:
                return jsonify({"error": str(exc)}), 409
        return jsonify({"job_id": handle.id, "kind": handle.kind})

    @app.route("/api/jobs/scan", methods=["POST"])
    def api_job_scan() -> Any:
        payload = request.get_json(silent=True) or {}
        categories = payload.get("categories") or None
        territories = int(payload.get("territories") or 0)
        refresh = bool(payload.get("refresh"))

        def job(database: Database, report) -> Dict[str, Any]:
            result = find_locations(
                settings, database, categories=categories,
                use_cache=not refresh, territories=territories,
                progress=lambda msg: report(msg),
            )
            return {
                "summary": f"{result.count:,} sites scored, {result.new_sites:,} new",
                "found": result.count,
                "new": result.new_sites,
            }

        return _start("scan", job, "Searching OpenStreetMap...")

    @app.route("/api/jobs/find-routes", methods=["POST"])
    def api_job_routes() -> Any:
        payload = request.get_json(silent=True) or {}
        ignore_robots = bool(payload.get("ignore_robots"))

        def job(database: Database, report) -> Dict[str, Any]:
            from ..routes.pipeline import find_routes

            result = find_routes(
                settings, database,
                respect_robots=False if ignore_robots else None,
                progress=lambda msg: report(msg),
            )
            blocked = [f"{r.label}: {r.skipped or r.error}"
                       for r in result.reports if r.skipped or r.error]
            database.set_setting("last_route_sources", json.dumps([
                {"name": r.name, "label": r.label, "status": r.status,
                 "found": len(r.listings), "why": r.skipped or r.error,
                 "fallback": r.used_fallback}
                for r in result.reports
            ]))
            return {
                "summary": f"{result.count} listing(s), {len(result.new_listings)} new",
                "found": result.count,
                "new": len(result.new_listings),
                "blocked": blocked,
            }

        return _start("find-routes", job, "Checking marketplaces...")

    @app.route("/api/jobs/send-queue", methods=["POST"])
    def api_job_send() -> Any:
        payload = request.get_json(silent=True) or {}
        dry_run = bool(payload.get("dry_run"))

        def job(database: Database, report) -> Dict[str, Any]:
            identity, smtp = identity_and_smtp(database)
            result = seq.process_queue(
                database, identity, smtp, dry_run=dry_run,
                progress=lambda msg: report(msg),
            )
            if result.blocked_reasons:
                raise RuntimeError(" ".join(result.blocked_reasons))
            verb = "would send" if dry_run else "sent"
            return {
                "summary": f"{result.sent} {verb}, {result.failed} failed, {result.skipped} skipped",
                "sent": result.sent, "failed": result.failed, "skipped": result.skipped,
                "details": result.details[:50],
            }

        return _start("send-queue", job, "Sending outreach...")

    @app.route("/api/autopilot/plan")
    def api_autopilot_plan() -> Any:
        """What one run would do, so it can be seen before it happens."""
        from ..autopilot import plan

        count = max(1, min(200, request.args.get("count", default=20, type=int)))
        min_score = request.args.get("min_score", default=65.0, type=float)
        with db() as database:
            identity, smtp = identity_and_smtp(database)
            result = plan(settings, database, identity, smtp,
                          count=count, min_score=min_score)
            return jsonify({
                "ready": result.ready,
                "need_lookup": result.need_lookup,
                "total": result.total_candidates,
                "daily_cap": result.daily_cap,
                "sent_today": result.sent_today,
                "remaining": result.remaining,
                "blocked_reasons": result.blocked_reasons,
                "has_ever_sent": result.has_ever_sent,
                "sample": result.sample,
            })

    @app.route("/api/jobs/autopilot", methods=["POST"])
    def api_job_autopilot() -> Any:
        """Pick the best prospects, find their details, write and send."""
        payload = request.get_json(silent=True) or {}
        count = max(1, min(200, int(payload.get("count") or 20)))
        min_score = float(payload.get("min_score") or 65.0)
        dry_run = bool(payload.get("dry_run"))

        def job(database: Database, report) -> Dict[str, Any]:
            from ..autopilot import run

            identity, smtp = identity_and_smtp(database)
            result = run(settings, database, identity, smtp, count=count,
                         min_score=min_score, dry_run=dry_run,
                         progress=lambda m: report(m))
            if result.blocked_reasons:
                raise RuntimeError(" ".join(result.blocked_reasons))
            return {
                "summary": result.summary,
                "picked": result.picked,
                "contacts_found": result.contacts_found,
                "enrolled": result.enrolled,
                "sent": result.sent,
                "failed": result.failed,
                "skipped": result.skipped[:40],
            }

        return _start("autopilot", job, "Choosing prospects...")

    @app.route("/api/jobs/find-contacts", methods=["POST"])
    def api_job_find_contacts() -> Any:
        """Read each prospect's own website for the contact details it lists."""
        payload = request.get_json(silent=True) or {}
        limit = max(1, min(400, int(payload.get("limit") or 60)))
        min_score = float(payload.get("min_score") or 0)
        site_ids = payload.get("site_ids") or []

        def job(database: Database, report) -> Dict[str, Any]:
            from ..enrich import enrich_sites

            if site_ids:
                wanted = set(site_ids)
                sites = [s for s in database.query_sites(limit=100_000) if s.id in wanted]
            else:
                # Best prospects first -- these are the ones worth the lookups.
                sites = database.query_sites(limit=limit, min_score=min_score)
            result = enrich_sites(settings, database, sites, progress=lambda m: report(m))
            return {
                "summary": result.summary,
                "checked": result.checked,
                "emails": result.emails_found,
                "phones": result.phones_found,
                "no_website": result.no_website,
                "nothing_found": result.nothing_found,
                "details": result.details[:60],
            }

        return _start("find-contacts", job, "Reading business websites...")

    @app.route("/api/jobs/check-replies", methods=["POST"])
    def api_job_check_replies() -> Any:
        payload = request.get_json(silent=True) or {}
        days = max(1, min(120, int(payload.get("days") or 30)))

        def job(database: Database, report) -> Dict[str, Any]:
            identity, _ = identity_and_smtp(database)
            result = check_replies(
                database, imap_config(database), identity,
                since_days=days, progress=lambda msg: report(msg),
            )
            if result.error:
                raise RuntimeError(result.error)
            found = len(result.replies)
            return {
                "summary": (f"{found} repl{'y' if found == 1 else 'ies'} found"
                            f" ({result.opt_outs} opted out)" if found
                            else "No new replies"),
                "found": found,
                "opt_outs": result.opt_outs,
                "scanned": result.scanned,
                "replies": [
                    {"site": r.site_name, "from": r.from_address,
                     "opted_out": r.opted_out, "body": r.body[:280]}
                    for r in result.replies[:50]
                ],
            }

        return _start("check-replies", job, "Checking your mailbox...")

    @app.route("/api/jobs/<int:job_id>")
    def api_job(job_id: int) -> Any:
        with db() as database:
            job = database.get_job(job_id)
        if not job:
            return jsonify({"error": "no such job"}), 404
        return jsonify(job)

    @app.route("/api/jobs/active")
    def api_job_active() -> Any:
        with db() as database:
            return jsonify({"job": database.active_job(), "recent": database.recent_jobs(5)})

    # ------------------------------------------------------------------ sites

    @app.route("/api/sites")
    def api_sites() -> Any:
        with db() as database:
            sites = database.query_sites(
                limit=request.args.get("limit", default=2000, type=int),
                min_score=request.args.get("min_score", default=0.0, type=float),
                category=request.args.get("category") or None,
                territory=request.args.get("territory", type=int),
                search=request.args.get("search") or None,
            )
            grade = (request.args.get("grade") or "").upper()
            if grade:
                sites = [s for s in sites if s.grade == grade]
            stages = {
                row["site_id"]: row["stage"]
                for row in database.conn.execute("SELECT site_id, stage FROM pipeline")
            }
            payload = []
            for site in sites:
                row = site.to_dict()
                row["sell_here"] = machine_recommendation(site)
                row["stage"] = stages.get(site.id, "new")
                payload.append(row)
        return jsonify({"count": len(payload), "sites": payload})

    @app.route("/api/sites/<path:site_id>")
    def api_site(site_id: str) -> Any:
        with db() as database:
            matches = [s for s in database.query_sites(limit=100_000) if s.id == site_id]
            if not matches:
                return jsonify({"error": "not found"}), 404
            site = matches[0]
            identity, _ = identity_and_smtp(database)
            pipeline = database.get_pipeline(site_id)
            data = site.to_dict()
            data["sell_here"] = machine_recommendation(site)
            return jsonify({
                "site": data,
                "pipeline": pipeline,
                "activities": database.site_activities(site_id),
                "messages": database.query_messages(site_id=site_id, limit=50),
                "suppressed": database.is_suppressed(
                    email=pipeline.get("contact_email") or site.email, site_id=site_id
                ),
                "merge_preview": build_context(site, identity, pipeline.get("contact_name") or ""),
            })

    @app.route("/api/sites/<path:site_id>/pipeline", methods=["POST"])
    def api_site_pipeline(site_id: str) -> Any:
        payload = request.get_json(silent=True) or {}
        stage = payload.get("stage")
        if stage and stage not in STAGE_KEYS:
            return jsonify({"error": f"unknown stage '{stage}'"}), 400
        with db() as database:
            before = database.get_pipeline(site_id)
            updated = database.update_pipeline(site_id, **payload)
            if stage and stage != before.get("stage"):
                database.add_activity(
                    site_id, "stage", f"Moved to {STAGE_LABELS.get(stage, stage)}",
                    "", {"from": before.get("stage"), "to": stage},
                )
                if stage in ("won", "lost"):
                    seq.cancel_pending(database, site_id, f"Marked {stage}")
            return jsonify(updated)

    @app.route("/api/sites/<path:site_id>/note", methods=["POST"])
    def api_site_note(site_id: str) -> Any:
        payload = request.get_json(silent=True) or {}
        body = str(payload.get("note", "")).strip()
        if not body:
            return jsonify({"error": "empty note"}), 400
        with db() as database:
            database.add_activity(site_id, "note", "Note", body[:2000])
        return jsonify({"ok": True})

    @app.route("/api/sites/<path:site_id>/reply", methods=["POST"])
    def api_site_reply(site_id: str) -> Any:
        payload = request.get_json(silent=True) or {}
        with db() as database:
            outcome = seq.record_reply(
                database, site_id, str(payload.get("text", "")),
                opted_out=bool(payload.get("opted_out")),
            )
        return jsonify(outcome)

    # --------------------------------------------------------------- pipeline

    @app.route("/api/pipeline")
    def api_pipeline() -> Any:
        limit = request.args.get("limit", default=60, type=int)
        with db() as database:
            board = {
                key: database.sites_in_stage(key, limit=limit) for key in STAGE_KEYS
            }
            counts = database.pipeline_counts()
            return jsonify({
                "board": board,
                "counts": counts,
                "stages": [{"key": key, "label": label, "count": counts.get(key, 0)}
                           for key, label in PIPELINE_STAGES],
            })

    # --------------------------------------------------------------- outreach

    @app.route("/api/outreach/preview", methods=["POST"])
    def api_outreach_preview() -> Any:
        payload = request.get_json(silent=True) or {}
        site_ids = payload.get("site_ids") or []
        with db() as database:
            identity, smtp = identity_and_smtp(database)
            steps = sequence_steps(database, payload.get("sequence", "intro"))
            sites = {s.id: s for s in database.query_sites(limit=100_000)}
            previews, blocked = [], []
            for site_id in site_ids[:200]:
                site = sites.get(site_id)
                if site is None:
                    continue
                pipeline = database.get_pipeline(site_id)
                email = (pipeline.get("contact_email") or site.email or "").strip()
                problem = recipient_problem(database, email, site_id)
                if not problem and database.has_been_contacted(site_id):
                    problem = "Already has outreach queued or sent"
                context = build_context(site, identity, pipeline.get("contact_name") or "")
                entry = {
                    "site_id": site_id, "name": site.name, "email": email,
                    "problem": problem,
                    "steps": [
                        {
                            "name": s["name"],
                            "delay_days": s.get("delay_days", 0),
                            "subject": render(s.get("subject", ""), context),
                            "body": render(s.get("body", ""), context),
                        }
                        for s in steps
                    ],
                }
                (blocked if problem else previews).append(entry)
            gate = check_send_gate(database, identity, smtp.is_configured)
            return jsonify({
                "ready": previews, "blocked": blocked,
                "gate": {"allowed": gate.allowed, "reasons": gate.reasons,
                         "sent_today": gate.sent_today, "daily_cap": gate.daily_cap},
                "steps": len(steps),
            })

    @app.route("/api/outreach/enroll", methods=["POST"])
    def api_outreach_enroll() -> Any:
        payload = request.get_json(silent=True) or {}
        site_ids = payload.get("site_ids") or []
        with db() as database:
            identity, _ = identity_and_smtp(database)
            missing = identity.missing_fields()
            if missing:
                return jsonify({
                    "error": "Finish your sender details in Settings first: "
                             + ", ".join(missing)
                }), 400
            sites = [s for s in database.query_sites(limit=100_000) if s.id in set(site_ids)]
            result = seq.enroll(
                database, sites, identity,
                sequence_key=payload.get("sequence", "intro"),
                contact_overrides=payload.get("contacts") or {},
            )
            return jsonify({
                "enrolled": result.count,
                "messages": result.messages_created,
                "skipped": result.skipped,
            })

    @app.route("/api/outreach/messages")
    def api_outreach_messages() -> Any:
        with db() as database:
            identity, smtp = identity_and_smtp(database)
            gate = check_send_gate(database, identity, smtp.is_configured)
            stats = database.outreach_stats()
            stats["due"] = len(
                database.query_messages(status="queued", due_only=True, limit=1000)
            )
            return jsonify({
                "messages": database.query_messages(
                    status=request.args.get("status") or None,
                    due_only=request.args.get("due") == "1",
                    limit=request.args.get("limit", default=200, type=int),
                ),
                "stats": stats,
                "gate": {"allowed": gate.allowed, "reasons": gate.reasons,
                         "sent_today": gate.sent_today, "daily_cap": gate.daily_cap,
                         "remaining": gate.remaining_today},
            })

    @app.route("/api/outreach/messages/<int:message_id>", methods=["POST"])
    def api_outreach_message_update(message_id: int) -> Any:
        payload = request.get_json(silent=True) or {}
        action = payload.get("action", "update")
        with db() as database:
            message = database.get_message(message_id)
            if not message:
                return jsonify({"error": "no such message"}), 404
            if action == "cancel":
                database.update_message(message_id, status="cancelled", error="Cancelled by you")
                return jsonify({"ok": True, "status": "cancelled"})
            if action == "requeue":
                database.update_message(message_id, status="queued", error="")
                return jsonify({"ok": True, "status": "queued"})
            database.update_message(
                message_id,
                subject=payload.get("subject", message["subject"]),
                body=payload.get("body", message["body"]),
                to_address=payload.get("to_address", message["to_address"]),
            )
            return jsonify({"ok": True})

    @app.route("/api/outreach/send/<int:message_id>", methods=["POST"])
    def api_outreach_send_one(message_id: int) -> Any:
        """Send a single message now, bypassing the schedule but not the gates."""
        from ..outreach.sender import SendError, send_email

        with db() as database:
            message = database.get_message(message_id)
            if not message:
                return jsonify({"error": "no such message"}), 404
            identity, smtp = identity_and_smtp(database)
            gate = check_send_gate(database, identity, smtp.is_configured)
            if not gate.allowed:
                return jsonify({"error": " ".join(gate.reasons)}), 400
            problem = recipient_problem(database, message["to_address"], message["site_id"])
            if problem:
                return jsonify({"error": problem}), 400
            try:
                sent_id = send_email(smtp, identity, message["to_address"],
                                     message["subject"], message["body"])
            except SendError as exc:
                database.update_message(message_id, status="failed", error=str(exc))
                return jsonify({"error": str(exc)}), 502
            database.update_message(message_id, status="sent", sent_at=utcnow(),
                                    message_id=sent_id, error="")
            database.add_activity(
                message["site_id"], "email_sent", f"Sent: {message['subject']}",
                message["body"][:500], {"to": message["to_address"]},
            )
            seq._advance_pipeline(database, message["site_id"], message)
            return jsonify({"ok": True})

    # -------------------------------------------------------------- templates

    @app.route("/api/templates", methods=["GET", "POST"])
    def api_templates() -> Any:
        with db() as database:
            if request.method == "GET":
                return jsonify({"templates": database.list_templates(),
                                "merge_fields": MERGE_FIELDS})
            payload = request.get_json(silent=True) or {}
            if not payload.get("key"):
                return jsonify({"error": "a template needs a key"}), 400
            existing = database.get_template(payload["key"])
            database.upsert_template({
                **(existing or {}),
                **payload,
                # Editing a built-in makes it yours; it is no longer reseeded.
                "builtin": False,
            })
            return jsonify({"ok": True, "template": database.get_template(payload["key"])})

    @app.route("/api/templates/<key>", methods=["DELETE"])
    def api_template_delete(key: str) -> Any:
        with db() as database:
            database.delete_template(key)
        return jsonify({"ok": True})

    # --------------------------------------------------------------- listings

    @app.route("/api/listings")
    def api_listings() -> Any:
        since = None
        days = request.args.get("since_days", type=int)
        if days:
            since = (datetime.now(timezone.utc)
                     - timedelta(days=days)).isoformat(timespec="seconds")
        with db() as database:
            listings = database.query_listings(
                limit=request.args.get("limit", default=300, type=int),
                min_relevance=request.args.get("min_relevance", default=0.0, type=float),
                local_only=request.args.get("local_only") == "1",
                max_price=request.args.get("max_price", type=float),
                min_price=request.args.get("min_price", type=float),
                min_machines=request.args.get("min_machines", type=int),
                with_financials=request.args.get("with_financials") == "1",
                search=request.args.get("search") or None,
                source=request.args.get("source") or None,
                since=since,
            )
            # The facets travel with every response so an empty list can say
            # whether the filters hid everything or nothing was ever found.
            return jsonify({
                "count": len(listings),
                "listings": [l.to_dict() for l in listings],
                "facets": database.listing_facets(),
                "sources_status": _loads_setting(database, "last_route_sources"),
            })

    @app.route("/api/listings/import", methods=["POST"])
    def api_listings_import() -> Any:
        """Import a broker's saved-search CSV export straight from the browser."""
        from ..routes.pipeline import import_csv

        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return jsonify({"error": "choose a CSV file first"}), 400
        settings.ensure_dirs()
        target = settings.cache_dir / f"import_{int(datetime.now().timestamp())}.csv"
        upload.save(str(target))
        try:
            with db() as database:
                listings = import_csv(
                    database, target,
                    source_name=request.form.get("source_name") or "imported",
                )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        finally:
            target.unlink(missing_ok=True)
        return jsonify({"imported": len(listings)})

    # ---------------------------------------------------------------- planner

    @app.route("/api/planner/route")
    def api_planner_route() -> Any:
        top = request.args.get("top", default=20, type=int)
        min_score = request.args.get("min_score", default=60.0, type=float)
        territory = request.args.get("territory", type=int)
        stage = request.args.get("stage") or None
        start_raw = request.args.get("start") or ""

        start = None
        if start_raw:
            try:
                lat_text, lon_text = start_raw.split(",")
                start = (float(lat_text), float(lon_text))
            except ValueError:
                return jsonify({"error": "start must look like '35.4676,-97.5164'"}), 400

        with db() as database:
            if stage:
                cards = database.sites_in_stage(stage, limit=top)
                ids = {c["id"] for c in cards}
                sites = [s for s in database.query_sites(limit=100_000) if s.id in ids][:top]
            else:
                sites = database.query_sites(
                    limit=top, min_score=min_score, territory=territory
                )
        if not sites:
            return jsonify({"stops": [], "distance_mi": 0, "error": "No sites match those filters."})

        route = plan_service_route(sites, start=start)
        stops = []
        for index, site in enumerate(route.stops, start=1):
            stops.append({
                "order": index, "id": site.id, "name": site.name,
                "address": site.address, "lat": site.lat, "lon": site.lon,
                "score": site.score, "grade": site.grade, "phone": site.phone,
            })
        waypoints = "/".join(f"{s['lat']},{s['lon']}" for s in stops[:24])
        return jsonify({
            "stops": stops,
            "distance_mi": round(route.distance_mi, 1),
            "start": start,
            "google_maps_url": f"https://www.google.com/maps/dir/{waypoints}" if waypoints else "",
        })

    @app.route("/api/territories", methods=["POST"])
    def api_territories() -> Any:
        payload = request.get_json(silent=True) or {}
        count = max(1, min(12, int(payload.get("count") or 4)))
        with db() as database:
            sites = database.query_sites(limit=100_000,
                                         min_score=float(payload.get("min_score") or 0))
            if not sites:
                return jsonify({"error": "Scan for prospects first."}), 400
            assignments = assign_territories(sites, count)
            database.update_site_territories(assignments)
            summary = []
            for index in range(count):
                members = [s for s in sites if s.territory == index]
                if not members:
                    continue
                summary.append({
                    "territory": index, "sites": len(members),
                    "avg_score": round(sum(s.score for s in members) / len(members), 1),
                    "lat": sum(s.lat for s in members) / len(members),
                    "lon": sum(s.lon for s in members) / len(members),
                })
        return jsonify({"territories": summary})

    # --------------------------------------------------------------- settings

    @app.route("/api/settings", methods=["GET", "POST"])
    def api_settings() -> Any:
        with db() as database:
            if request.method == "GET":
                stored = dict(database.all_settings())
                # Never echo a secret back to the browser.
                for secret in ("smtp_password", "imap_password", "session_secret"):
                    stored.pop(secret, None)
                return jsonify(stored)
            payload = request.get_json(silent=True) or {}
            for key, value in payload.items():
                if key in ("smtp_password", "imap_password") and not value:
                    continue  # blank means "leave the saved one alone"
                database.set_setting(str(key), str(value))
            identity, smtp = identity_and_smtp(database)
            return jsonify({
                "ok": True,
                "missing": identity.missing_fields(),
                "smtp_configured": smtp.is_configured,
            })

    @app.route("/api/settings/test-smtp", methods=["POST"])
    def api_test_smtp() -> Any:
        with db() as database:
            _, smtp = identity_and_smtp(database)
        ok, message = test_connection(smtp)
        return jsonify({"ok": ok, "message": message})

    @app.route("/api/settings/test-imap", methods=["POST"])
    def api_test_imap() -> Any:
        """Open the mailbox and read one message, so a wrong setting shows up
        here rather than as a silent no-op later."""
        with db() as database:
            config = imap_config(database)
        if not config.is_configured:
            return jsonify({"ok": False,
                            "message": "Fill in the mail server, username and password first."})
        try:
            messages = fetch_raw_messages(config, since_days=3)
        except InboxError as exc:
            return jsonify({"ok": False, "message": str(exc)})
        return jsonify({
            "ok": True,
            "message": f"Connected. {len(messages)} message(s) in the last three days.",
        })

    @app.route("/api/password", methods=["POST"])
    def api_password() -> Any:
        """Set, change or remove the login password from the browser."""
        payload = request.get_json(silent=True) or {}
        action = payload.get("action", "set")
        with db() as database:
            if auth.password_is_from_env():
                return jsonify({
                    "error": "The password is set by MACHINE_LOCATOR_PASSWORD "
                             "where this is hosted, so it can't be changed here."
                }), 400

            already = auth.has_password(database)
            # Changing or removing an existing password requires the old one.
            if already and not auth.check_password(database, payload.get("current", "")):
                return jsonify({"error": "Your current password isn't right."}), 400

            if action == "remove":
                if app.config.get("PUBLIC_INSTANCE"):
                    return jsonify({
                        "error": "This instance is reachable from the internet, "
                                 "so it has to keep a password."
                    }), 400
                auth.clear_password(database)
                session.clear()
                return jsonify({"ok": True, "protected": False})

            chosen = str(payload.get("password", ""))
            problem = auth.password_problem(chosen, payload.get("confirm"))
            if problem:
                return jsonify({"error": problem}), 400
            auth.set_password(database, chosen)
            session["authenticated"] = True
            session.permanent = True
            return jsonify({"ok": True, "protected": True})

    @app.route("/api/suppression", methods=["GET", "POST"])
    def api_suppression() -> Any:
        with db() as database:
            if request.method == "GET":
                return jsonify({"entries": database.suppression_list()})
            payload = request.get_json(silent=True) or {}
            value = str(payload.get("value", "")).strip()
            if not value:
                return jsonify({"error": "nothing to add"}), 400
            database.suppress(value, payload.get("kind", "email"),
                              payload.get("reason", "Added manually"))
            return jsonify({"ok": True, "entries": database.suppression_list()})

    @app.route("/api/suppression/<path:value>", methods=["DELETE"])
    def api_unsuppress(value: str) -> Any:
        with db() as database:
            database.unsuppress(value)
            return jsonify({"ok": True, "entries": database.suppression_list()})

    # ----------------------------------------------------------------- export

    @app.route("/download/<what>.<fmt>")
    def download(what: str, fmt: str) -> Any:
        settings.ensure_dirs()
        path = settings.export_dir / f"{what}.{fmt}"
        with db() as database:
            if what == "prospects":
                sites = database.query_sites(
                    limit=100_000,
                    min_score=request.args.get("min_score", default=0.0, type=float),
                )
                if not sites:
                    return jsonify({"error": "nothing to export yet"}), 400
                if fmt == "geojson":
                    sites_to_geojson(sites, path)
                elif fmt == "csv":
                    sites_to_csv(sites, path)
                else:
                    return jsonify({"error": "use csv or geojson"}), 400
            elif what == "listings":
                listings = database.query_listings(limit=100_000)
                if not listings:
                    return jsonify({"error": "nothing to export yet"}), 400
                if fmt == "csv":
                    listings_to_csv(listings, path)
                elif fmt == "json":
                    listings_to_json(listings, path)
                else:
                    return jsonify({"error": "use csv or json"}), 400
            else:
                return jsonify({"error": "unknown export"}), 404
        return send_file(str(path), as_attachment=True, download_name=path.name)

    @app.errorhandler(500)
    def on_error(exc):  # pragma: no cover - defensive
        return jsonify({"error": "Something went wrong on the server."}), 500

    return app


def main() -> None:  # pragma: no cover
    create_app().run(debug=True)


if __name__ == "__main__":  # pragma: no cover
    main()
