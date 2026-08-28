"""One action that does the whole outreach run.

Picking prospects by hand is the step that stops the work happening at all, so
this collapses it: choose the best businesses that have not been approached,
find their contact details, write each one a personalised sequence, and send
what is due today.

Every guardrail the manual path has still applies -- it uses the same enrolment
and the same sender, so the compliance footer, the do-not-contact list, the
daily cap and the reply handling are not bypassed, because they are not
reimplemented here.

Three things it does on purpose:

* **It fills the batch with businesses it can actually email.** Asking for
  twenty means twenty emails go out, not "twenty picked, eleven sendable" --
  it works down the ranked list, reading websites as it goes, until it has
  twenty addresses. The ranking still decides the order; only the businesses
  with nowhere to write to are stepped over.
* **It sizes the batch to what is actually sendable today.** Queueing eighty
  emails against a cap of forty just means half of them go out tomorrow under
  yesterday's assumptions; picking forty means what you approve is what happens.
* **It never invents an address.** Prospects whose website lists no contact
  details are left alone for a phone call, not filled in with a guess -- a
  bounce costs more than a blank.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

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
    no_contact: List[Dict[str, Any]] = field(default_factory=list)
    target: int = 0
    daily_cap: int = 0
    sent_today: int = 0
    remaining: int = 0
    blocked_reasons: List[str] = field(default_factory=list)
    has_ever_sent: bool = False
    sample: Optional[Dict[str, str]] = None

    @property
    def total_candidates(self) -> int:
        return len(self.ready) + len(self.need_lookup)

    @property
    def will_write_to(self) -> int:
        """The most it could send: the target, or however many it can reach."""
        return min(self.target, self.total_candidates)


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
            if self.skipped:
                return (f"No reachable prospects — {len(self.skipped)} looked at, "
                        "none publish an email address")
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


def _reachability(db, site: Site) -> Tuple[str, str]:
    """``(address, '')`` if we can write to them, ``('', reason)`` if we can't.

    Both empty means nothing is on file *yet* -- which is the one case worth
    spending a website lookup on. A stated reason (bad address, opted out) is
    final, and saying so beats reporting it later as "no email found".
    """
    address = _address_for(db, site)
    if not address:
        return "", ""
    problem = recipient_problem(db, address, site.id)
    return ("", problem) if problem else (address, "")


def _website_for(site: Site) -> str:
    return (site.website or (site.tags or {}).get("contact:website", "") or "").strip()


def collect_sendable(
    settings: Settings,
    db,
    target: int,
    min_score: float,
    progress=None,
    max_lookups: int = 0,
) -> Dict[str, Any]:
    """Gather ``target`` prospects that can actually be emailed.

    Works down the ranked list rather than taking a fixed slice off the top:
    a business with no published address is stepped over and the next one takes
    its place, so asking for twenty gets twenty emails rather than twenty
    attempts. Websites are read in small batches only while the batch is still
    short, so a list that is already full costs no lookups at all.
    """
    say = progress or (lambda _m: None)
    empty = {"sites": [], "passed_over": [], "lookups": 0,
             "contacts_found": 0, "pool": 0}
    if target <= 0:
        return empty

    max_lookups = max_lookups or max(target * 5, 30)
    pool = candidates(db, max(target * 8, 60), min_score)
    if not pool:
        return empty

    sendable: List[Site] = []
    passed_over: List[Dict[str, str]] = []
    already_read: set = set()      # site ids we have already spent a lookup on
    lookups = 0
    contacts_found = 0
    index = 0

    while index < len(pool) and len(sendable) < target:
        site = pool[index]
        address, problem = _reachability(db, site)

        if address:
            sendable.append(site)
            index += 1
            continue
        if problem:
            passed_over.append({"name": site.name, "reason": problem})
            index += 1
            continue
        if site.id in already_read:
            passed_over.append({"name": site.name,
                                "reason": "No email published on their website"})
            index += 1
            continue
        if not _website_for(site):
            passed_over.append({"name": site.name,
                                "reason": "No website to look at"})
            index += 1
            continue
        if lookups >= max_lookups:
            break

        # Read the next few sites that need it in one polite pass, then come
        # back to this same one -- ``already_read`` is what ends the loop.
        room = min(max(target - len(sendable), 4), max_lookups - lookups)
        chunk: List[Site] = []
        for later in pool[index:]:
            if len(chunk) >= room:
                break
            later_address, later_problem = _reachability(db, later)
            if later.id in already_read or later_address or later_problem:
                continue
            if not _website_for(later):
                continue
            chunk.append(later)
            already_read.add(later.id)
        if not chunk:
            break

        from .enrich import enrich_sites

        say(f"{len(sendable)} of {target} ready — reading "
            f"{len(chunk)} business website(s)...")
        found = enrich_sites(settings, db, chunk, progress=say)
        lookups += len(chunk)
        contacts_found += found.emails_found

    return {"sites": sendable, "passed_over": passed_over, "lookups": lookups,
            "contacts_found": contacts_found, "pool": len(pool)}


def plan(
    settings: Settings,
    db,
    identity: SenderIdentity,
    smtp: SmtpConfig,
    count: int = 20,
    min_score: float = 65.0,
) -> AutopilotPlan:
    """Work out what a run would do, so it can be shown before it happens.

    Reads nothing over the network -- it sorts the ranked list into the three
    groups a run would meet, so the dialog can say how many are reachable now
    and how many websites it would have to read to fill the rest.
    """
    gate = check_send_gate(db, identity, smtp.is_configured)
    batch = max(0, min(count, gate.remaining_today or count))
    result = AutopilotPlan(
        target=batch,
        daily_cap=gate.daily_cap,
        sent_today=gate.sent_today,
        remaining=gate.remaining_today,
        blocked_reasons=gate.reasons,
        has_ever_sent=db.outreach_stats().get("sent", 0) > 0,
    )

    budget = max(batch * 5, 30)
    for site in candidates(db, max(batch * 8, 60), min_score):
        address, problem = _reachability(db, site)
        entry = {
            "site_id": site.id, "name": site.name, "score": site.score,
            "grade": site.grade, "type": site.category_label,
            "address": site.address, "email": address,
        }
        if address:
            result.ready.append(entry)
            if len(result.ready) >= batch:
                break                     # no lookups needed past this point
        elif problem:
            entry["reason"] = problem
            result.no_contact.append(entry)
        elif _website_for(site) and len(result.need_lookup) < budget:
            result.need_lookup.append(entry)
        else:
            entry["reason"] = "No website to look at"
            result.no_contact.append(entry)

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

    say("Finding the best prospects we can actually email...")
    gathered = collect_sendable(settings, db, batch, min_score, progress=say)
    sendable: List[Site] = gathered["sites"]
    result.picked = len(sendable)
    result.looked_up = gathered["lookups"]
    result.contacts_found = gathered["contacts_found"]
    result.skipped = list(gathered["passed_over"])

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
