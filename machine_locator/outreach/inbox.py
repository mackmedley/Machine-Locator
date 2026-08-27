"""Reading replies out of the operator's mailbox over IMAP.

This is the half of the automation that was missing. Sending on a schedule is
easy; the part that makes an outreach sequence safe to leave running is noticing
when somebody answers -- because the worst outcome is a prospect who wrote back
"yes please" still getting a day-11 "sorry I missed you" note, and the second
worst is someone who said stop getting one anyway.

Matching a reply to what we sent is done twice over:

1. ``In-Reply-To`` / ``References`` against the Message-ID we recorded when the
   mail went out. Exact, when the client sends it.
2. Otherwise the sender's address against the last thing we sent them. Many
   clients drop or rewrite threading headers, so this fallback is what makes
   detection work in practice.

Nothing is deleted or marked read on the server -- the mailbox is the operator's
and this only reads.
"""

from __future__ import annotations

import email
import imaplib
import re
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.utils import parseaddr
from typing import Dict, List, Optional, Tuple

from .compliance import SenderIdentity, looks_like_opt_out

PASSWORD_ENV_VAR = "MACHINE_LOCATOR_IMAP_PASSWORD"

COMMON_HOSTS = {
    "gmail.com": ("imap.gmail.com", 993),
    "googlemail.com": ("imap.gmail.com", 993),
    "outlook.com": ("outlook.office365.com", 993),
    "hotmail.com": ("outlook.office365.com", 993),
    "yahoo.com": ("imap.mail.yahoo.com", 993),
    "icloud.com": ("imap.mail.me.com", 993),
}

# Where a reply stops being the reply and starts being a quote of our own email.
QUOTE_MARKERS = (
    re.compile(r"^\s*On .+ wrote:\s*$", re.IGNORECASE),
    re.compile(r"^\s*-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE),
    re.compile(r"^\s*_{5,}\s*$"),
    re.compile(r"^\s*From:\s.+@", re.IGNORECASE),
    re.compile(r"^\s*Sent from my \w+", re.IGNORECASE),
)


@dataclass
class ImapConfig:
    host: str = ""
    port: int = 993
    username: str = ""
    password: str = ""
    folder: str = "INBOX"

    @classmethod
    def from_settings(cls, settings: Dict[str, str]) -> "ImapConfig":
        import os

        password = (
            os.environ.get(PASSWORD_ENV_VAR)
            or settings.get("imap_password", "")
            # Most people use one account for both, so fall back to the SMTP one.
            or os.environ.get("MACHINE_LOCATOR_SMTP_PASSWORD")
            or settings.get("smtp_password", "")
        )
        try:
            port = int(settings.get("imap_port") or 993)
        except (TypeError, ValueError):
            port = 993
        return cls(
            host=settings.get("imap_host", "").strip(),
            port=port,
            username=(settings.get("imap_username", "").strip()
                      or settings.get("smtp_username", "").strip()),
            password=password,
            folder=settings.get("imap_folder", "INBOX").strip() or "INBOX",
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.username and self.password)

    @staticmethod
    def suggest(email_address: str) -> Optional[Tuple[str, int]]:
        if "@" not in (email_address or ""):
            return None
        return COMMON_HOSTS.get(email_address.split("@", 1)[1].strip().lower())


class InboxError(RuntimeError):
    pass


def _decode(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (UnicodeDecodeError, LookupError, ValueError):
        return value


def plain_text(message: email.message.Message) -> str:
    """The readable body of a message, preferring text/plain."""
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain" and \
                    "attachment" not in str(part.get("Content-Disposition", "")):
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        return ""
    payload = message.get_payload(decode=True)
    if payload is None:
        return str(message.get_payload() or "")
    charset = message.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def strip_quoted(text: str) -> str:
    """Keep what the person actually wrote, drop the quoted email underneath.

    Getting this right matters beyond tidiness: our own sent copy contains the
    word "stop" in its opt-out footer, so classifying the whole quoted blob
    would read every reply as an opt-out.
    """
    kept: List[str] = []
    for line in (text or "").splitlines():
        if any(marker.match(line) for marker in QUOTE_MARKERS):
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


@dataclass
class DetectedReply:
    site_id: str
    site_name: str
    from_address: str
    subject: str
    body: str
    opted_out: bool
    message_id: str


@dataclass
class InboxResult:
    replies: List[DetectedReply] = field(default_factory=list)
    scanned: int = 0
    unmatched: int = 0
    already_handled: int = 0
    error: str = ""

    @property
    def opt_outs(self) -> int:
        return sum(1 for r in self.replies if r.opted_out)


def fetch_raw_messages(config: ImapConfig, since_days: int = 30) -> List[bytes]:
    """Download recent messages from the mailbox. Read-only."""
    if not config.is_configured:
        raise InboxError("IMAP is not configured")

    since = (datetime.now() - timedelta(days=max(1, since_days))).strftime("%d-%b-%Y")
    context = ssl.create_default_context()
    try:
        server = imaplib.IMAP4_SSL(config.host, config.port, ssl_context=context)
    except (imaplib.IMAP4.error, OSError, ssl.SSLError) as exc:
        raise InboxError(f"Could not reach {config.host}: {exc}") from exc

    try:
        try:
            server.login(config.username, config.password)
        except imaplib.IMAP4.error as exc:
            raise InboxError(
                "The mail server rejected your username or password. Gmail "
                "accounts with 2-factor authentication need an App Password."
            ) from exc

        # readonly=True: never flag or delete anything in the operator's mailbox.
        status, _ = server.select(config.folder, readonly=True)
        if status != "OK":
            raise InboxError(f"Could not open the folder '{config.folder}'")

        status, data = server.search(None, "SINCE", since)
        if status != "OK":
            raise InboxError("The mailbox search failed")

        ids = (data[0] or b"").split()
        raw: List[bytes] = []
        for message_id in ids[-500:]:  # a sane ceiling on a busy mailbox
            status, payload = server.fetch(message_id, "(RFC822)")
            if status != "OK" or not payload:
                continue
            for part in payload:
                if isinstance(part, tuple) and part[1]:
                    raw.append(part[1])
                    break
        return raw
    finally:
        try:
            server.close()
        except Exception:
            pass
        try:
            server.logout()
        except Exception:
            pass


def match_replies(db, raw_messages: List[bytes], own_address: str = "") -> InboxResult:
    """Turn raw mailbox messages into replies we can act on."""
    result = InboxResult()
    own = (own_address or "").strip().lower()

    for raw in raw_messages:
        try:
            message = email.message_from_bytes(raw)
        except Exception:
            continue
        result.scanned += 1

        sender = parseaddr(message.get("From", ""))[1].strip().lower()
        if not sender or (own and sender == own):
            continue  # our own copy in a "sent" folder is not a reply

        message_id = (message.get("Message-ID", "") or "").strip()
        if message_id and db.is_reply_handled(message_id):
            result.already_handled += 1
            continue

        # Exact threading match first, then the address fallback.
        sent = None
        refs = " ".join(filter(None, [message.get("In-Reply-To", ""),
                                      message.get("References", "")]))
        for candidate in re.findall(r"<[^>]+>", refs):
            sent = db.find_sent_by_message_id(candidate)
            if sent:
                break
        if sent is None:
            sent = db.find_sent_by_recipient(sender)
        if sent is None:
            result.unmatched += 1
            continue

        body = strip_quoted(plain_text(message))
        subject = _decode(message.get("Subject", ""))
        result.replies.append(DetectedReply(
            site_id=sent["site_id"],
            site_name=sent.get("site_name") or sent.get("to_address", ""),
            from_address=sender,
            subject=subject,
            body=body,
            # The subject alone is a poor signal ("Re: ..." echoes our words),
            # so classification runs on what they actually typed.
            opted_out=looks_like_opt_out(body),
            message_id=message_id,
        ))

    return result


def check_replies(
    db,
    config: ImapConfig,
    identity: SenderIdentity,
    since_days: int = 30,
    progress=None,
) -> InboxResult:
    """Fetch, match and act on replies: log them and stop live sequences."""
    from .sequences import record_reply

    if progress:
        progress("Connecting to your mailbox...")
    try:
        raw = fetch_raw_messages(config, since_days=since_days)
    except InboxError as exc:
        return InboxResult(error=str(exc))

    if progress:
        progress(f"Reading {len(raw)} recent message(s)...")
    result = match_replies(db, raw, own_address=identity.sender_email)

    for reply in result.replies:
        record_reply(db, reply.site_id, reply.body or reply.subject,
                     opted_out=reply.opted_out)
        db.mark_reply_handled(reply.message_id, reply.site_id)
    return result
