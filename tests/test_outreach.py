import smtplib

import pytest

from machine_locator.models import Site
from machine_locator.outreach.compliance import (
    SenderIdentity, check_send_gate, compliance_footer, looks_like_opt_out,
    recipient_problem, with_footer,
)
from machine_locator.outreach.sender import SmtpConfig, build_message, send_email, SendError
from machine_locator.outreach.sequences import cancel_pending, enroll, process_queue, record_reply
from machine_locator.outreach.templates import (
    build_context, install_builtins, render, sequence_steps,
)

FULL = dict(
    business_name="Sooner Vending", sender_name="Mack",
    sender_email="mack@sooner.example", postal_address="1 Main St, OKC, OK 73106",
    business_phone="(405) 555-0134", commission_line="15% of gross",
)


def identity(**overrides):
    return SenderIdentity.from_settings({**FULL, **overrides})


def make_site(site_id="node/1", name="Suds Laundromat", email="owner@suds.example"):
    return Site(
        id=site_id, name=name, category="laundromat", category_label="Laundromat",
        lat=35.4676, lon=-97.5164, address="123 NW 23rd St, Oklahoma City, OK 73106",
        score=88.0, grade="A+", email=email,
        reasons=["no store, cafe or drive-thru within 400m"],
        tags={"addr:city": "Oklahoma City"},
    )


# ------------------------------------------------------------- compliance

def test_identity_requires_the_can_spam_fields():
    assert identity().is_complete
    assert "Your physical mailing address (required by CAN-SPAM)" in \
        identity(postal_address="").missing_fields()
    assert "Your business name" in identity(business_name="").missing_fields()


def test_identity_rejects_a_malformed_sender_address():
    assert "A valid sender email address" in identity(sender_email="not-an-email").missing_fields()


def test_footer_carries_address_and_opt_out():
    footer = compliance_footer(identity())
    assert "1 Main St, OKC, OK 73106" in footer
    assert "STOP" in footer
    assert "Sooner Vending" in footer


def test_footer_is_applied_once_not_twice():
    once = with_footer("Hello", identity())
    assert with_footer(once, identity()) == once


@pytest.mark.parametrize("text", [
    "STOP", "stop.", "  Stop  ", "please unsubscribe", "Take me off your list",
    "not interested, thanks", "Do not contact us again", "stop emailing me",
    "Please remove me from your list", "no thanks",
])
def test_opt_out_detection(text):
    assert looks_like_opt_out(text)


@pytest.mark.parametrize("text", [
    "Sure, come by Tuesday", "How much does it cost?", "",
    # The expensive false positives: a warm reply must never be read as an
    # opt-out, because suppression is permanent.
    "Sure - stop by Thursday afternoon",
    "Yes please, stop in any time this week",
    "We're a one-stop shop so foot traffic is good",
    "I stopped by your website, looks good",
])
def test_non_opt_out_replies(text):
    assert not looks_like_opt_out(text)


def test_send_gate_blocks_without_identity_or_smtp(db):
    gate = check_send_gate(db, identity(postal_address=""), smtp_configured=False)
    assert not gate.allowed
    assert any("postal" in r.lower() or "mailing" in r.lower() for r in gate.reasons)
    assert any("mail server" in r for r in gate.reasons)


def test_send_gate_opens_when_everything_is_set(db):
    assert check_send_gate(db, identity(), smtp_configured=True).allowed


def test_send_gate_enforces_the_daily_cap(db):
    db.set_setting("daily_send_cap", "2")
    for i in range(2):
        message_id = db.add_message({"site_id": "n/1", "to_address": "a@b.com"})
        db.update_message(message_id, status="sent", sent_at="2999-01-01T00:00:00+00:00")
    gate = check_send_gate(db, identity(), smtp_configured=True)
    assert not gate.allowed
    assert "Daily cap reached" in gate.reasons[0]


def test_recipient_problem_cases(db):
    assert recipient_problem(db, "", "n/1") == "No email address on file"
    assert "not a valid" in recipient_problem(db, "nope", "n/1")
    assert recipient_problem(db, "ok@example.com", "n/1") == ""
    db.suppress("ok@example.com")
    assert recipient_problem(db, "ok@example.com", "n/1") == "On your do-not-contact list"


def test_suppressing_a_domain_covers_every_address_on_it(db):
    db.suppress("example.com", "domain", "bounced")
    assert db.is_suppressed(email="anyone@example.com")
    assert not db.is_suppressed(email="anyone@other.com")


# -------------------------------------------------------------- templates

def test_render_shows_unknown_fields_instead_of_raising():
    assert render("Hi {nope}", {}) == "Hi [nope]"
    assert render("Hi {name}", {"name": "Sam"}) == "Hi Sam"


def test_render_survives_a_stray_brace():
    assert "{" in render("50% off { today", {})


def test_build_context_pulls_from_the_site_and_identity():
    context = build_context(make_site(), identity(), contact_name="Dana")
    assert context["business_name"] == "Suds Laundromat"
    assert context["contact_name"] == "Dana"
    assert context["city"] == "Oklahoma City"
    assert context["commission_line"] == "15% of gross"
    assert "cold drink" in context["machine_types"]


def test_context_falls_back_to_there_when_no_contact_name():
    assert build_context(make_site(), identity())["contact_name"] == "there"


def test_fit_reason_is_phrased_for_the_recipient():
    # The scorer's own wording ("captive audience") is not something to say to
    # the person being pitched.
    reason = build_context(make_site(), identity())["fit_reason"]
    assert "captive" not in reason.lower()
    assert reason


def test_builtin_templates_install_once(db):
    assert install_builtins(db) == 5
    assert install_builtins(db) == 0


def test_builtin_edit_is_not_overwritten_by_reinstall(db):
    install_builtins(db)
    db.upsert_template({"key": "intro_email", "name": "Mine", "body": "custom", "builtin": False})
    install_builtins(db)
    assert db.get_template("intro_email")["body"] == "custom"


def test_sequence_steps_are_ordered(db):
    install_builtins(db)
    steps = sequence_steps(db)
    assert [s["step"] for s in steps] == [0, 1, 2]
    assert [s["delay_days"] for s in steps] == [0, 4, 11]


def test_rendered_intro_email_has_no_unfilled_fields(db):
    install_builtins(db)
    context = build_context(make_site(), identity(), "Dana")
    body = render(sequence_steps(db)[0]["body"], context)
    assert "{" not in body and "[" not in body
    assert "Suds Laundromat" in body
    assert "no cost to you" in body


def test_the_pitch_never_opens_with_a_cut_of_the_sales(db):
    """Deliberate sales decision: lead with free and no hassle. Revenue share
    is an answer to a question, not part of the opening offer."""
    install_builtins(db)
    context = build_context(make_site(), identity(), "Dana")
    for step in sequence_steps(db):
        body = render(step["body"], context).lower()
        assert "15% of gross" not in body
        assert "you get" not in body


def test_the_scripts_carry_the_answer_for_when_they_ask(db):
    install_builtins(db)
    context = build_context(make_site(), identity(), "Dana")
    for key in ("walk_in_script", "call_script"):
        body = render(db.get_template(key)["body"], context)
        assert "15% of gross" in body
        assert "ask" in body.lower()


# ----------------------------------------------------------------- sender

def test_build_message_sets_headers_and_footer():
    message = build_message(identity(), "them@example.com", "Subject", "Body")
    assert message["To"] == "them@example.com"
    assert "Sooner Vending" in message["From"]
    assert message["List-Unsubscribe"]
    assert "1 Main St" in message.get_content()


def test_dry_run_sends_nothing():
    # A dry run builds and validates the message but never connects, so there
    # is no Message-ID to hand back.
    assert send_email(SmtpConfig(), identity(), "a@b.com", "s", "b", dry_run=True) == ""


def test_send_without_config_raises():
    with pytest.raises(SendError, match="not configured"):
        send_email(SmtpConfig(), identity(), "a@b.com", "s", "b")


def test_auth_failure_explains_app_passwords(monkeypatch):
    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def ehlo(self): pass
        def starttls(self, **k): pass
        def login(self, u, p): raise smtplib.SMTPAuthenticationError(535, b"nope")

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    config = SmtpConfig(host="h", port=587, username="u", password="p")
    with pytest.raises(SendError, match="App Password"):
        send_email(config, identity(), "a@b.com", "s", "b")


def test_smtp_config_reads_password_from_the_environment(monkeypatch):
    monkeypatch.setenv("MACHINE_LOCATOR_SMTP_PASSWORD", "from-env")
    config = SmtpConfig.from_settings({"smtp_host": "h", "smtp_username": "u",
                                       "smtp_password": "from-db"})
    assert config.password == "from-env"


def test_smtp_host_suggestion():
    assert SmtpConfig.suggest("me@gmail.com") == ("smtp.gmail.com", 587)
    assert SmtpConfig.suggest("me@my-own-domain.com") is None


# -------------------------------------------------------------- sequences

def test_enroll_creates_every_step(db):
    install_builtins(db)
    db.upsert_sites([make_site()])
    result = enroll(db, [make_site()], identity())
    assert result.count == 1
    assert result.messages_created == 3
    assert db.get_pipeline("node/1")["stage"] == "queued"
    assert len(db.site_activities("node/1")) == 1


def test_enroll_skips_missing_and_suppressed_addresses(db):
    install_builtins(db)
    db.suppress("blocked@x.com")
    sites = [
        make_site("node/1", "No Email", email=""),
        make_site("node/2", "Blocked", email="blocked@x.com"),
    ]
    result = enroll(db, sites, identity())
    assert result.count == 0
    reasons = {s["reason"] for s in result.skipped}
    assert reasons == {"No email address on file", "On your do-not-contact list"}


def test_cancel_pending_stops_the_rest(db):
    install_builtins(db)
    enroll(db, [make_site()], identity())
    assert cancel_pending(db, "node/1", "changed my mind") == 3
    assert db.query_messages(status="queued", site_id="node/1") == []


def test_record_reply_opt_out_suppresses_domain_and_site(db):
    install_builtins(db)
    enroll(db, [make_site()], identity())
    outcome = record_reply(db, "node/1", "STOP")
    assert outcome["opted_out"] and outcome["cancelled"] == 3
    assert db.is_suppressed(email="owner@suds.example")
    assert db.is_suppressed(site_id="node/1")
    assert db.get_pipeline("node/1")["stage"] == "lost"


def test_process_queue_dry_run_sends_nothing_but_reports(db):
    install_builtins(db)
    db.upsert_sites([make_site()])
    enroll(db, [make_site()], identity())
    result = process_queue(db, identity(), SmtpConfig(), dry_run=True)
    assert result.sent == 0
    assert result.blocked_reasons == []
    assert db.outreach_stats()["sent"] == 0


def test_process_queue_is_blocked_by_the_gate(db):
    install_builtins(db)
    enroll(db, [make_site()], identity())
    result = process_queue(db, identity(postal_address=""), SmtpConfig())
    assert result.sent == 0
    assert result.blocked_reasons


def test_process_queue_sends_and_advances_the_pipeline(db, monkeypatch):
    install_builtins(db)
    db.upsert_sites([make_site()])
    enroll(db, [make_site()], identity())

    sent = []
    monkeypatch.setattr(
        "machine_locator.outreach.sequences.send_email",
        lambda *a, **k: (sent.append(a[2]), "<abc123@sooner.example>")[1],
    )
    config = SmtpConfig(host="h", port=587, username="u", password="p")
    result = process_queue(db, identity(), config)

    # Only step 0 is due today; the day-4 and day-11 notes stay queued.
    assert result.sent == 1
    assert sent == ["owner@suds.example"]
    assert db.get_pipeline("node/1")["stage"] == "contacted"
    assert len(db.query_messages(status="queued", site_id="node/1")) == 2
    # The Message-ID is kept so a reply can be threaded back to this exact email.
    delivered = db.query_messages(status="sent", site_id="node/1")[0]
    assert delivered["message_id"] == "<abc123@sooner.example>"


def test_process_queue_skips_someone_who_opted_out_after_queueing(db, monkeypatch):
    install_builtins(db)
    db.upsert_sites([make_site()])
    enroll(db, [make_site()], identity())
    db.suppress("owner@suds.example", reason="opted out later")

    monkeypatch.setattr(
        "machine_locator.outreach.sequences.send_email",
        lambda *a, **k: pytest.fail("must not send to a suppressed address"),
    )
    result = process_queue(db, identity(), SmtpConfig(host="h", username="u", password="p"))
    assert result.sent == 0 and result.skipped == 1


def test_process_queue_records_a_send_failure(db, monkeypatch):
    install_builtins(db)
    db.upsert_sites([make_site()])
    enroll(db, [make_site()], identity())

    def boom(*a, **k):
        raise SendError("mailbox full")

    monkeypatch.setattr("machine_locator.outreach.sequences.send_email", boom)
    result = process_queue(db, identity(), SmtpConfig(host="h", username="u", password="p"))
    assert result.failed == 1
    assert db.outreach_stats()["failed"] == 1


def test_from_line_falls_back_when_a_name_is_missing():
    only_business = build_message(
        identity(sender_name=""), "them@example.com", "s", "b"
    )
    assert "Sooner Vending" in only_business["From"]


def test_templates_list_leads_with_the_email_sequence(db):
    install_builtins(db)
    keys = [t["key"] for t in db.list_templates()]
    assert keys[:3] == ["intro_email", "followup_1", "followup_2"]
    assert set(keys[3:]) == {"call_script", "walk_in_script"}
