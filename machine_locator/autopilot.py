"""One action that does the whole outreach run.

Picking prospects by hand is the step that stops the work happening at all, so
this collapses it: choose the best businesses that have not been approached,
find their contact details, write each one a personalised sequence, and send
what is due today.

Every guardrail the manual path has still applies -- it uses the same enrolment
and the same sender, so the compliance footer, the do-not-contact list, the
daily cap and the reply handling are not bypassed, because they are not
reimplemented here.

Two things it does on purpose:

* **It sizes the batch to what is actually sendable today.** Queueing eighty
  emails against a cap of forty just means half of them go out tomorrow under
  yesterday's assumptions; picking forty means what you approve is what happens.
* **It never invents an address.** Prospects whose website lists no contact
  details are left alone for a phone call, not filled in with a guess -- a
  bounce costs more than a blank.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .config import Settings
from .models import Site
from .outreach.compliance import SenderIdentity, check_send_gate, recipient_problem
from .outreach.sender import SmtpConfig
from .outreach.sequences import enroll, process_queue


@dataclass
class AutopilotPlan:
    """What a run would do, before it does any of it."""

    ready: List[Dict[str, Any]] = field(default_factory=list)
    need_lookup: List[Dict[str, Any]] = field(default_factory=list)
    daily_cap: int = 0
    sent_today: int = 0
    remaining: int = 0
    blocked_reasons: List[str] = field(default_factory=list)
    has_ever_sent: bool = False
    sample: Optional[Dict[str, str]] = None

    @property
    def total_candidates(self) -> int:
        return len(self.ready) + len(self.need_lookup)


@dataclass
class AutopilotResult:
    picked: int = 0
    looked_up: int = 0
    contacts_found: int = 0
    enrolled: int = 0
    sent: int = 0
    failed: int = 0
    skipped: List[Dict[str, str]] = field(default_factory=list)
    blocked_reasons: List[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.blocked_reasons:
            return "Nothing sent — " + self.blocked_reasons[0]
        if not self.picked:
            return "No new prospects to approach"
        return (f"{self.sent} email(s) sent to {self.enrolled} new business(es)"
                if self.sent else
                f"{self.enrolled} business(es) queued, nothing due to send yet")


def candidates(db, limit: int, min_score: float) -> List[Site]:
    """The best prospects nobody has approached yet.

    Ordered by score, so the machine works the same list a person would work
    from the top of.
    """
    picked: List[Site] = []
    for site in db.query_sites(limit=max(limit * 6, 60), min_score=min_score):
        pipeline = db.get_pipeline(site.id)
        if pipeline.get("stage", "new") not in ("new", "queued"):
            continue                      # somebody is already working this one
        if db.has_been_contacted(site.id):
            continue
        if db.is_suppressed(site_id=site.id,
                            email=pipeline.get("contact_email") or site.email):
            continue
        picked.append(site)
        if len(picked) >= limit:
            break
    return picked


def _address_for(db, site: Site) -> str:
    pipeline = db.get_pipeline(site.id)
    return (pipeline.get("contact_email") or site.email or "").strip()


def plan(
    settings: Settings,
    db,
    identity: SenderIdentity,
    smtp: SmtpConfig,
    count: int = 20,
    min_score: float = 65.0,
) -> AutopilotPlan:
    """Work out what a run would do, so it can be shown before it happens."""
    gate = check_send_gate(db, identity, smtp.is_configured)
    result = AutopilotPlan(
        daily_cap=gate.daily_cap,
        sent_today=gate.sent_today,
        remaining=gate.remaining_today,
        blocked_reasons=gate.reasons,
        has_ever_sent=db.outreach_stats().get("sent", 0) > 0,
    )

    batch = max(0, min(count, gate.remaining_today or count))
    for site in candidates(db, batch, min_score):
        address = _address_for(db, site)
        entry = {
            "site_id": site.id, "name": site.name, "score": site.score,
            "grade": site.grade, "type": site.category_label,
            "address": site.address, "email": address,
        }
        if address and not recipient_problem(db, address, site.id):
            result.ready.append(entry)
        else:
            result.need_lookup.append(entry)

    if result.ready or result.need_lookup:
        from .outreach.templates import build_context, render, sequence_steps

        steps = sequence_steps(db)
        first = (result.ready + result.need_lookup)[0]
        site = next((s for s in db.query_sites(limit=100_000)
                     if s.id == first["site_id"]), None)
        if site and steps:
            context = build_context(site, identity)
            result.sample = {
                "name": site.name,
                "subject": render(steps[0].get("subject", ""), context),
                "body": render(steps[0].get("body", ""), context),
            }
    return result


def run(
    settings: Settings,
    db,
    identity: SenderIdentity,
    smtp: SmtpConfig,
    count: int = 20,
    min_score: float = 65.0,
    dry_run: bool = False,
    progress=None,
) -> AutopilotResult:
    """Pick, look up, write and send. The whole thing."""
    say = progress or (lambda _m: None)
    result = AutopilotResult()

    gate = check_send_gate(db, identity, smtp.is_configured or dry_run)
    if not gate.allowed:
        result.blocked_reasons = gate.reasons
        return result

    batch = max(0, min(count, gate.remaining_today))
    if not batch:
        result.blocked_reasons = [
            f"Today's cap is used up ({gate.sent_today} of {gate.daily_cap} sent)."
        ]
        return result

    say("Choosing the best prospects nobody has approached...")
    chosen = candidates(db, batch, min_score)
    result.picked = len(chosen)
    if not chosen:
        return result

    missing = [s for s in chosen if not _address_for(db, s)]
    if missing:
        from .enrich import enrich_sites

        say(f"Looking up contact details for {len(missing)} business(es)...")
        found = enrich_sites(settings, db, missing, progress=say)
        result.looked_up = found.checked
        result.contacts_found = found.emails_found

    sendable: List[Site] = []
    for site in chosen:
        address = _address_for(db, site)
        problem = recipient_problem(db, address, site.id)
        if problem:
            result.skipped.append({"name": site.name, "reason": problem})
            continue
        sendable.append(site)

    if not sendable:
        return result

    say(f"Writing to {len(sendable)} business(es)...")
    enrolled = enroll(db, sendable, identity)
    result.enrolled = enrolled.count
    for skip in enrolled.skipped:
        result.skipped.append({"name": skip["name"], "reason": skip["reason"]})

    say("Sending...")
    sent = process_queue(db, identity, smtp, dry_run=dry_run, limit=batch, progress=say)
    result.sent = sent.sent
    result.failed = sent.failed
    if sent.blocked_reasons:
        result.blocked_reasons = sent.blocked_reasons
    return result
