"""Compliance gates for commercial outreach.

Cold B2B email is lawful in the US under the CAN-SPAM Act, but only when it
meets specific requirements. Rather than documenting them and hoping, this
module makes them structural:

* You cannot send until a real business name, sender address and **physical
  postal address** are configured -- the postal address is a hard CAN-SPAM
  requirement, so it is a hard gate here.
* Every message gets an opt-out mechanism and that postal address appended.
  The footer is not optional and not editable away.
* An opt-out goes straight to a suppression list that is checked on every
  send, including for the domain, so one "stop" covers the whole company.
* A daily cap limits blast size, because a hundred identical emails in an hour
  is how a sending domain gets burned.

Phone and SMS are deliberately **not** automated. Cold automated texting falls
under the TCPA, which requires prior express consent and carries statutory
damages per message; the tool generates call scripts and click-to-call links
for a human to dial instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

DEFAULT_DAILY_CAP = 40

# The settings that must exist before a single message can go out.
REQUIRED_IDENTITY_FIELDS = (
    ("business_name", "Your business name"),
    ("sender_name", "Your name"),
    ("sender_email", "Your email address"),
    ("postal_address", "Your physical mailing address (required by CAN-SPAM)"),
)


@dataclass
class SenderIdentity:
    business_name: str = ""
    sender_name: str = ""
    sender_email: str = ""
    reply_to: str = ""
    postal_address: str = ""
    phone: str = ""
    website: str = ""
    commission_line: str = "a share of the sales"
    city: str = "Oklahoma City"

    @classmethod
    def from_settings(cls, settings: Dict[str, str]) -> "SenderIdentity":
        return cls(
            business_name=settings.get("business_name", ""),
            sender_name=settings.get("sender_name", ""),
            sender_email=settings.get("sender_email", ""),
            reply_to=settings.get("reply_to", "") or settings.get("sender_email", ""),
            postal_address=settings.get("postal_address", ""),
            phone=settings.get("business_phone", ""),
            website=settings.get("business_website", ""),
            commission_line=settings.get("commission_line", "a share of the sales"),
            city=settings.get("city", "Oklahoma City"),
        )

    def missing_fields(self) -> List[str]:
        problems = []
        for key, label in REQUIRED_IDENTITY_FIELDS:
            if not str(getattr(self, key, "")).strip():
                problems.append(label)
        if self.sender_email and not EMAIL_RE.match(self.sender_email.strip()):
            problems.append("A valid sender email address")
        return problems

    @property
    def is_complete(self) -> bool:
        return not self.missing_fields()


def compliance_footer(identity: SenderIdentity) -> str:
    """The block appended to every outgoing email. Not optional.

    Carries the two things CAN-SPAM requires and a recipient reasonably
    expects: who is writing and where they are, and how to make it stop.
    """
    lines = [
        "--",
        f"{identity.sender_name} | {identity.business_name}",
    ]
    contact = " | ".join(p for p in (identity.phone, identity.website) if p)
    if contact:
        lines.append(contact)
    lines.append(identity.postal_address)
    lines.append("")
    lines.append(
        "You received this because we identified your business as a possible "
        "site for a vending machine. If you would rather not hear from us, "
        'reply with "STOP" and we will remove you from our list immediately '
        "and permanently."
    )
    return "\n".join(lines)


def with_footer(body: str, identity: SenderIdentity) -> str:
    footer = compliance_footer(identity)
    if footer.strip() in body:
        return body
    return f"{body.rstrip()}\n\n{footer}\n"


# Phrases that mean "stop" and cannot mean anything else.
OPT_OUT_PHRASES = (
    "unsubscribe", "remove me", "take me off", "opt out", "opt-out",
    "do not contact", "don't contact", "do not email", "don't email",
    "stop emailing", "stop contacting", "stop sending", "stop messaging",
    "no longer wish", "not interested", "no thank you", "no thanks",
)

# A bare "STOP" is the opt-out keyword our own footer asks for, so it counts --
# but only when it is the entire message. "Sure, stop by Thursday" is a warm
# reply, and suppressing that address forever would be the worst possible
# outcome of a keyword match.
_BARE_STOP = re.compile(r"^\s*(stop|stop\.|stop!|unsubscribe)\s*$", re.IGNORECASE)


def looks_like_opt_out(text: str) -> bool:
    """Whether a reply should be treated as an opt-out request.

    Errs toward catching real opt-outs, but never on a bare substring: matching
    "stop" anywhere would swallow "stop by", "one-stop" and "stopped in", and
    an opt-out is irreversible.
    """
    raw = (text or "").strip()
    if not raw:
        return False
    if _BARE_STOP.match(raw):
        return True
    lowered = " ".join(raw.lower().split())
    return any(phrase in lowered for phrase in OPT_OUT_PHRASES)


def start_of_today_utc() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


@dataclass
class SendGate:
    """The verdict on whether sending is allowed right now."""

    allowed: bool
    reasons: List[str]
    sent_today: int = 0
    daily_cap: int = DEFAULT_DAILY_CAP

    @property
    def remaining_today(self) -> int:
        return max(0, self.daily_cap - self.sent_today)


def check_send_gate(db, identity: SenderIdentity, smtp_configured: bool) -> SendGate:
    """Everything that must be true before any message leaves the building."""
    reasons: List[str] = []

    missing = identity.missing_fields()
    if missing:
        reasons.append("Missing required sender details: " + ", ".join(missing))
    if not smtp_configured:
        reasons.append(
            "No outgoing mail server configured. Add your SMTP details in Settings "
            "so mail is sent from your own address."
        )

    try:
        cap = int(db.get_setting("daily_send_cap", str(DEFAULT_DAILY_CAP)) or DEFAULT_DAILY_CAP)
    except (TypeError, ValueError):
        cap = DEFAULT_DAILY_CAP
    sent_today = db.messages_sent_since(start_of_today_utc())
    if sent_today >= cap:
        reasons.append(
            f"Daily cap reached ({sent_today}/{cap} sent today). "
            "This protects your sending reputation -- raise it in Settings if you mean to."
        )

    return SendGate(
        allowed=not reasons, reasons=reasons, sent_today=sent_today, daily_cap=cap
    )


def recipient_problem(db, email: str, site_id: str) -> str:
    """Why this specific recipient cannot be mailed, or '' if they can."""
    address = (email or "").strip()
    if not address:
        return "No email address on file"
    if not EMAIL_RE.match(address):
        return f"'{address}' is not a valid email address"
    if db.is_suppressed(email=address, site_id=site_id):
        return "On your do-not-contact list"
    return ""
