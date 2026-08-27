"""Sending mail through the operator's own mail server.

Mail goes out over the user's own SMTP account -- their Gmail, their domain --
not a shared relay. That is deliberate: the sending reputation, the replies and
the accountability all belong to the person doing the outreach, which is both
the honest arrangement and the one that actually lands in inboxes.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Optional, Tuple

from .compliance import SenderIdentity, with_footer

# Set this instead of storing the password in the local database.
PASSWORD_ENV_VAR = "MACHINE_LOCATOR_SMTP_PASSWORD"

COMMON_HOSTS = {
    "gmail.com": ("smtp.gmail.com", 587),
    "googlemail.com": ("smtp.gmail.com", 587),
    "outlook.com": ("smtp-mail.outlook.com", 587),
    "hotmail.com": ("smtp-mail.outlook.com", 587),
    "yahoo.com": ("smtp.mail.yahoo.com", 587),
    "icloud.com": ("smtp.mail.me.com", 587),
}


@dataclass
class SmtpConfig:
    host: str = ""
    port: int = 587
    username: str = ""
    password: str = ""
    use_tls: bool = True     # STARTTLS on a submission port
    use_ssl: bool = False    # implicit TLS, usually port 465

    @classmethod
    def from_settings(cls, settings: dict) -> "SmtpConfig":
        # An environment variable always wins over the stored value, so a user
        # who would rather not keep a password in SQLite does not have to.
        password = os.environ.get(PASSWORD_ENV_VAR) or settings.get("smtp_password", "")
        try:
            port = int(settings.get("smtp_port") or 587)
        except (TypeError, ValueError):
            port = 587
        return cls(
            host=settings.get("smtp_host", "").strip(),
            port=port,
            username=settings.get("smtp_username", "").strip(),
            password=password,
            use_tls=settings.get("smtp_security", "starttls") == "starttls",
            use_ssl=settings.get("smtp_security", "starttls") == "ssl",
        )

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.username and self.password)

    @staticmethod
    def suggest(email: str) -> Optional[Tuple[str, int]]:
        """Guess host and port from an email domain, for the settings form."""
        if "@" not in (email or ""):
            return None
        return COMMON_HOSTS.get(email.split("@", 1)[1].strip().lower())


class SendError(RuntimeError):
    pass


def build_message(
    identity: SenderIdentity, to_address: str, subject: str, body: str
) -> EmailMessage:
    """Assemble the email, with the compliance footer applied here.

    The footer is added at build time rather than at compose time so it cannot
    be edited out of a draft between preview and send.
    """
    message = EmailMessage()
    message["Subject"] = subject
    # A cold email from "Mack" is easy to ignore; "Mack - Sooner Vending" tells
    # the recipient who is writing before they open it.
    display = " - ".join(
        part for part in (identity.sender_name, identity.business_name) if part
    ) or identity.sender_email
    message["From"] = formataddr((display, identity.sender_email))
    message["To"] = to_address
    if identity.reply_to and identity.reply_to != identity.sender_email:
        message["Reply-To"] = identity.reply_to
    message["Message-ID"] = make_msgid()
    # Tells well-behaved mail systems how to unsubscribe without a reply.
    message["List-Unsubscribe"] = f"<mailto:{identity.reply_to or identity.sender_email}?subject=unsubscribe>"
    message.set_content(with_footer(body, identity))
    return message


def send_email(
    config: SmtpConfig,
    identity: SenderIdentity,
    to_address: str,
    subject: str,
    body: str,
    dry_run: bool = False,
    timeout: int = 30,
) -> str:
    """Send one message. Returns the Message-ID it went out with.

    The caller stores that id, which is what lets a reply be matched back to
    the exact email it answers. ``dry_run`` builds and validates the message
    but connects to nothing.
    """
    message = build_message(identity, to_address, subject, body)
    sent_id = message["Message-ID"]
    if dry_run:
        return ""
    if not config.is_configured:
        raise SendError("SMTP is not configured")

    context = ssl.create_default_context()
    try:
        if config.use_ssl:
            server = smtplib.SMTP_SSL(config.host, config.port, timeout=timeout,
                                      context=context)
        else:
            server = smtplib.SMTP(config.host, config.port, timeout=timeout)
        with server:
            server.ehlo()
            if config.use_tls and not config.use_ssl:
                server.starttls(context=context)
                server.ehlo()
            server.login(config.username, config.password)
            server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise SendError(
            "The mail server rejected your username or password. "
            "If you use Gmail with 2-factor authentication you need an App "
            "Password, not your normal password."
        ) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise SendError(f"The mail server refused the recipient {to_address}") from exc
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise SendError(f"Could not send: {type(exc).__name__}: {exc}") from exc
    return sent_id


def test_connection(config: SmtpConfig, timeout: int = 20) -> Tuple[bool, str]:
    """Log in and immediately disconnect, so Settings can verify before sending."""
    if not config.is_configured:
        return False, "Fill in the mail server, username and password first."
    context = ssl.create_default_context()
    try:
        if config.use_ssl:
            server = smtplib.SMTP_SSL(config.host, config.port, timeout=timeout,
                                      context=context)
        else:
            server = smtplib.SMTP(config.host, config.port, timeout=timeout)
        with server:
            server.ehlo()
            if config.use_tls and not config.use_ssl:
                server.starttls(context=context)
                server.ehlo()
            server.login(config.username, config.password)
    except smtplib.SMTPAuthenticationError:
        return False, (
            "Username or password rejected. Gmail accounts with 2-factor "
            "authentication need an App Password."
        )
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, "Connected and signed in successfully."
