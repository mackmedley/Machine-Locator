"""Enrolling prospects in a follow-up sequence and working the send queue.

A single cold email gets ignored; the second and third are where the replies
come from. So outreach here is a *sequence*: an intro, a nudge four days later,
and a final note at day eleven, each scheduled up front and each cancellable in
one move the moment somebody replies or opts out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from ..models import Site, utcnow
from .compliance import SenderIdentity, check_send_gate, recipient_problem
from .sender import SmtpConfig, SendError, send_email
from .templates import INTRO_SEQUENCE, build_context, render, sequence_steps


def _iso_in_days(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat(timespec="seconds")


@dataclass
class EnrollResult:
    enrolled: List[str] = field(default_factory=list)
    skipped: List[Dict[str, str]] = field(default_factory=list)
    messages_created: int = 0

    @property
    def count(self) -> int:
        return len(self.enrolled)


def enroll(
    db,
    sites: Sequence[Site],
    identity: SenderIdentity,
    sequence_key: str = INTRO_SEQUENCE,
    contact_overrides: Optional[Dict[str, Dict[str, str]]] = None,
) -> EnrollResult:
    """Queue a whole sequence for each prospect.

    Every step is created up front with its scheduled date, so the user can see
    exactly what will go out and when, and so cancelling is one update rather
    than a background scheduler to reason about.
    """
    steps = sequence_steps(db, sequence_key)
    result = EnrollResult()
    if not steps:
        return result

    overrides = contact_overrides or {}

    for site in sites:
        pipeline = db.get_pipeline(site.id)
        override = overrides.get(site.id, {})
        email = (
            override.get("contact_email")
            or pipeline.get("contact_email")
            or site.email
            or ""
        ).strip()
        contact_name = override.get("contact_name") or pipeline.get("contact_name") or ""

        problem = recipient_problem(db, email, site.id)
        if problem:
            result.skipped.append({"site_id": site.id, "name": site.name, "reason": problem})
            continue
        if db.has_been_contacted(site.id):
            result.skipped.append({
                "site_id": site.id, "name": site.name,
                "reason": "Already has outreach queued or sent",
            })
            continue

        context = build_context(site, identity, contact_name)
        for step in steps:
            db.add_message({
                "site_id": site.id,
                "channel": "email",
                "template_key": step["key"],
                "sequence_key": sequence_key,
                "step": int(step.get("step", 0)),
                "to_address": email,
                "subject": render(step.get("subject", ""), context),
                "body": render(step.get("body", ""), context),
                "status": "queued",
                "scheduled_at": _iso_in_days(int(step.get("delay_days", 0))),
            })
            result.messages_created += 1

        db.update_pipeline(
            site.id, stage="queued", contact_email=email,
            contact_name=contact_name, next_action="Intro email queued",
            next_action_at=_iso_in_days(0),
        )
        db.add_activity(
            site.id, "enrolled", "Added to outreach sequence",
            f"{len(steps)} messages queued to {email}",
            {"sequence": sequence_key},
        )
        result.enrolled.append(site.id)

    return result


def cancel_pending(db, site_id: str, reason: str = "") -> int:
    """Cancel every queued message for a prospect. Used on reply and opt-out."""
    pending = [m for m in db.query_messages(status="queued", site_id=site_id)]
    for message in pending:
        db.update_message(message["id"], status="cancelled", error=reason)
    if pending:
        db.add_activity(
            site_id, "sequence_stopped", "Remaining follow-ups cancelled",
            reason or "Sequence stopped", {"cancelled": len(pending)},
        )
    return len(pending)


def record_reply(db, site_id: str, text: str, opted_out: bool = False) -> Dict[str, Any]:
    """Log a reply, stop the sequence, and suppress if they asked to be left alone."""
    from .compliance import looks_like_opt_out

    is_opt_out = opted_out or looks_like_opt_out(text)
    cancelled = cancel_pending(
        db, site_id, "Opted out" if is_opt_out else "Prospect replied"
    )
    db.add_activity(
        site_id, "reply", "Opted out" if is_opt_out else "Reply received",
        text, {"opt_out": is_opt_out},
    )

    pipeline = db.get_pipeline(site_id)
    if is_opt_out:
        email = pipeline.get("contact_email") or ""
        if email:
            db.suppress(email, "email", "Opted out of outreach")
        db.suppress(site_id, "site", "Opted out of outreach")
        db.update_pipeline(site_id, stage="lost", next_action="", next_action_at="")
    else:
        db.update_pipeline(
            site_id, stage="interested", next_action="Reply -- follow up",
            next_action_at=_iso_in_days(0),
        )
    return {"opted_out": is_opt_out, "cancelled": cancelled}


@dataclass
class SendRunResult:
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    blocked_reasons: List[str] = field(default_factory=list)
    details: List[Dict[str, str]] = field(default_factory=list)


def process_queue(
    db,
    identity: SenderIdentity,
    smtp: SmtpConfig,
    dry_run: bool = False,
    limit: int = 100,
    progress=None,
) -> SendRunResult:
    """Send every queued message whose scheduled time has arrived.

    Stops at the daily cap rather than pushing through it, and re-checks the
    suppression list for each recipient -- somebody may have opted out after
    the message was queued.
    """
    result = SendRunResult()
    gate = check_send_gate(db, identity, smtp.is_configured or dry_run)
    if not gate.allowed:
        result.blocked_reasons = gate.reasons
        return result

    due = db.query_messages(status="queued", due_only=True, limit=limit)
    budget = gate.remaining_today

    for index, message in enumerate(due, start=1):
        if budget <= 0:
            result.skipped += 1
            continue
        if progress:
            progress(f"Sending {index} of {len(due)}...")

        site_id = message["site_id"]
        to_address = message.get("to_address", "")
        problem = recipient_problem(db, to_address, site_id)
        if problem:
            db.update_message(message["id"], status="cancelled", error=problem)
            db.add_activity(site_id, "send_skipped", "Message not sent", problem)
            result.skipped += 1
            result.details.append({"site": message.get("site_name", ""), "result": problem})
            continue

        try:
            outcome = send_email(
                smtp, identity, to_address,
                message.get("subject", ""), message.get("body", ""),
                dry_run=dry_run,
            )
            db.update_message(
                message["id"],
                status="sent" if not dry_run else "queued",
                sent_at=utcnow() if not dry_run else "",
                error="",
            )
            if not dry_run:
                budget -= 1
                result.sent += 1
                db.add_activity(
                    site_id, "email_sent", f"Sent: {message.get('subject', '')}",
                    message.get("body", "")[:500], {"to": to_address},
                )
                _advance_pipeline(db, site_id, message)
            else:
                result.skipped += 1
            result.details.append({"site": message.get("site_name", ""), "result": outcome})
        except SendError as exc:
            db.update_message(message["id"], status="failed", error=str(exc))
            db.add_activity(site_id, "send_failed", "Send failed", str(exc))
            result.failed += 1
            result.details.append({"site": message.get("site_name", ""), "result": str(exc)})

    return result


def _advance_pipeline(db, site_id: str, message: Dict[str, Any]) -> None:
    """Move a prospect along as their sequence progresses."""
    pipeline = db.get_pipeline(site_id)
    stage = pipeline.get("stage", "new")
    step = int(message.get("step", 0))

    if stage in ("won", "lost", "interested"):
        return  # a human has already moved this one; leave it alone

    remaining = [
        m for m in db.query_messages(status="queued", site_id=site_id)
    ]
    if remaining:
        next_at = min(m.get("scheduled_at") or "" for m in remaining)
        db.update_pipeline(
            site_id,
            stage="contacted" if step == 0 else "following_up",
            next_action=f"Follow-up {len(remaining)} of {len(remaining)} queued",
            next_action_at=next_at,
        )
    else:
        db.update_pipeline(
            site_id, stage="following_up",
            next_action="Sequence finished -- call or walk in",
            next_action_at=_iso_in_days(3),
        )
