from email.message import EmailMessage

import pytest

from machine_locator.models import Site
from machine_locator.outreach.compliance import SenderIdentity, compliance_footer
from machine_locator.outreach.inbox import (
    ImapConfig, check_replies, match_replies, plain_text, strip_quoted,
)
from machine_locator.outreach.sequences import enroll
from machine_locator.outreach.templates import install_builtins

IDENTITY = SenderIdentity.from_settings({
    "business_name": "Sooner Vending", "sender_name": "Mack",
    "sender_email": "mack@sooner.example",
    "postal_address": "1 Main St, OKC, OK 73106",
    "commission_line": "15% of gross",
})

OURS = "<sent-001@sooner.example>"


def make_site(site_id="node/1", email="owner@suds.example"):
    return Site(
        id=site_id, name="Suds Laundromat", category="laundromat",
        category_label="Laundromat", lat=35.4676, lon=-97.5164,
        address="123 NW 23rd St, Oklahoma City, OK", score=88.0, grade="A+",
        email=email, reasons=["no store, cafe or drive-thru within 400m"],
    )


def raw_reply(body, sender="owner@suds.example", subject="Re: Vending machine?",
              in_reply_to=OURS, message_id="<incoming-1@suds.example>"):
    message = EmailMessage()
    message["From"] = f"Dana <{sender}>"
    message["To"] = "mack@sooner.example"
    message["Subject"] = subject
    message["Message-ID"] = message_id
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    message.set_content(body)
    return message.as_bytes()


@pytest.fixture
def seeded(db):
    """One site with a sent intro whose Message-ID we recorded."""
    install_builtins(db)
    db.upsert_sites([make_site()])
    enroll(db, [make_site()], IDENTITY)
    first = db.query_messages(status="queued", site_id="node/1")[0]
    db.update_message(first["id"], status="sent",
                      sent_at="2026-08-20T10:00:00+00:00", message_id=OURS)
    return db


# ------------------------------------------------------------ quote stripping

def test_strip_quoted_removes_the_quoted_original():
    text = ("Sounds good, come by Thursday.\n\n"
            "On Tue, 25 Aug 2026 at 09:00, Mack wrote:\n"
            "> We install the machine at no cost to you.\n"
            "> Reply with STOP to be removed.\n")
    assert strip_quoted(text) == "Sounds good, come by Thursday."


def test_strip_quoted_drops_our_own_opt_out_footer():
    """Our footer contains the word STOP. Classifying the quoted blob instead
    of what the person typed would read every reply as an opt-out."""
    footer = compliance_footer(IDENTITY)
    text = f"Yes please!\n\nOn Tue, Mack wrote:\n> intro\n> {footer}"
    cleaned = strip_quoted(text)
    assert cleaned == "Yes please!"
    assert "STOP" not in cleaned


def test_strip_quoted_handles_outlook_style_dividers():
    text = "No thanks.\n\n-----Original Message-----\nFrom: mack@sooner.example\nblah"
    assert strip_quoted(text) == "No thanks."


def test_plain_text_prefers_the_text_part():
    message = EmailMessage()
    message.set_content("the plain version")
    message.add_alternative("<p>the html version</p>", subtype="html")
    assert "plain version" in plain_text(message)


# ---------------------------------------------------------------- matching

def test_reply_matched_by_threading_header(seeded):
    result = match_replies(seeded, [raw_reply("Sure, stop by Thursday")],
                           own_address=IDENTITY.sender_email)
    assert len(result.replies) == 1
    reply = result.replies[0]
    assert reply.site_id == "node/1"
    assert reply.opted_out is False


def test_reply_matched_by_sender_when_headers_are_missing(seeded):
    """Plenty of clients drop In-Reply-To; the address fallback is what makes
    detection work in practice."""
    result = match_replies(seeded, [raw_reply("Interested", in_reply_to=None)],
                           own_address=IDENTITY.sender_email)
    assert len(result.replies) == 1
    assert result.replies[0].site_id == "node/1"


def test_opt_out_is_classified_from_the_typed_text(seeded):
    result = match_replies(seeded, [raw_reply("Please unsubscribe me")],
                           own_address=IDENTITY.sender_email)
    assert result.replies[0].opted_out is True
    assert result.opt_outs == 1


def test_warm_reply_mentioning_stop_by_is_not_an_opt_out(seeded):
    result = match_replies(seeded, [raw_reply("Sure - stop by Thursday afternoon")],
                           own_address=IDENTITY.sender_email)
    assert result.replies[0].opted_out is False


def test_mail_from_a_stranger_is_ignored(seeded):
    result = match_replies(
        seeded, [raw_reply("Buy my SEO services", sender="spam@elsewhere.example",
                           in_reply_to=None)],
        own_address=IDENTITY.sender_email,
    )
    assert result.replies == []
    assert result.unmatched == 1


def test_our_own_copy_is_not_treated_as_a_reply(seeded):
    result = match_replies(
        seeded, [raw_reply("intro copy", sender=IDENTITY.sender_email, in_reply_to=None)],
        own_address=IDENTITY.sender_email,
    )
    assert result.replies == []


def test_an_already_handled_reply_is_skipped(seeded):
    seeded.mark_reply_handled("<incoming-1@suds.example>", "node/1")
    result = match_replies(seeded, [raw_reply("hello again")],
                           own_address=IDENTITY.sender_email)
    assert result.replies == []
    assert result.already_handled == 1


def test_unparseable_message_does_not_sink_the_batch(seeded):
    result = match_replies(seeded, [b"\xff\xfe not an email", raw_reply("yes please")],
                           own_address=IDENTITY.sender_email)
    assert len(result.replies) == 1


# ------------------------------------------------------------------ acting

def test_check_replies_stops_the_sequence_and_moves_the_stage(seeded, monkeypatch):
    monkeypatch.setattr(
        "machine_locator.outreach.inbox.fetch_raw_messages",
        lambda config, since_days=30: [raw_reply("Yes please, let's talk")],
    )
    result = check_replies(seeded, ImapConfig(host="h", username="u", password="p"), IDENTITY)
    assert len(result.replies) == 1
    assert seeded.get_pipeline("node/1")["stage"] == "interested"
    assert seeded.query_messages(status="queued", site_id="node/1") == []


def test_check_replies_suppresses_on_opt_out(seeded, monkeypatch):
    monkeypatch.setattr(
        "machine_locator.outreach.inbox.fetch_raw_messages",
        lambda config, since_days=30: [raw_reply("unsubscribe please")],
    )
    check_replies(seeded, ImapConfig(host="h", username="u", password="p"), IDENTITY)
    assert seeded.is_suppressed(email="owner@suds.example")
    assert seeded.get_pipeline("node/1")["stage"] == "lost"


def test_running_twice_does_not_double_handle(seeded, monkeypatch):
    monkeypatch.setattr(
        "machine_locator.outreach.inbox.fetch_raw_messages",
        lambda config, since_days=30: [raw_reply("Sounds good")],
    )
    config = ImapConfig(host="h", username="u", password="p")
    first = check_replies(seeded, config, IDENTITY)
    second = check_replies(seeded, config, IDENTITY)
    assert len(first.replies) == 1
    assert second.replies == []
    assert second.already_handled == 1


def test_connection_failure_is_reported_not_raised(seeded, monkeypatch):
    from machine_locator.outreach.inbox import InboxError

    def boom(config, since_days=30):
        raise InboxError("Could not reach imap.example: refused")

    monkeypatch.setattr("machine_locator.outreach.inbox.fetch_raw_messages", boom)
    result = check_replies(seeded, ImapConfig(host="h", username="u", password="p"), IDENTITY)
    assert result.error and result.replies == []


# --------------------------------------------------------------- config

def test_imap_config_falls_back_to_the_sending_account():
    config = ImapConfig.from_settings({
        "imap_host": "imap.example.com",
        "smtp_username": "me@example.com", "smtp_password": "shared",
    })
    assert config.username == "me@example.com"
    assert config.password == "shared"
    assert config.is_configured


def test_imap_password_env_var_wins(monkeypatch):
    monkeypatch.setenv("MACHINE_LOCATOR_IMAP_PASSWORD", "from-env")
    config = ImapConfig.from_settings({"imap_host": "h", "imap_username": "u",
                                       "imap_password": "from-db"})
    assert config.password == "from-env"


def test_imap_host_suggestion():
    assert ImapConfig.suggest("me@gmail.com") == ("imap.gmail.com", 993)
    assert ImapConfig.suggest("me@my-domain.com") is None
