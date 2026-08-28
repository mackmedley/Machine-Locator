import pytest

from machine_locator.autopilot import candidates, plan, run
from machine_locator.models import Site
from machine_locator.outreach.compliance import SenderIdentity
from machine_locator.outreach.sender import SmtpConfig
from machine_locator.outreach.templates import install_builtins

IDENTITY = SenderIdentity.from_settings({
    "business_name": "Blue Ox Vending", "sender_name": "Mack",
    "sender_email": "mack@blueox.example",
    "postal_address": "412 NW 23rd St, Oklahoma City, OK 73103",
    "business_phone": "405-397-2784",
})
SMTP = SmtpConfig(host="smtp.example", port=587, username="u", password="p")


def make_site(n, score=80.0, email="", name=None):
    return Site(
        id=f"node/{n}", name=name or f"Business {n}", category="laundromat",
        category_label="Laundromat", lat=35.4676 + n / 1000, lon=-97.5164,
        address=f"{n} Main St, Oklahoma City, OK", score=score, grade="A",
        email=email, reasons=["no store, cafe or drive-thru within 400m"],
    )


@pytest.fixture
def stocked(db):
    install_builtins(db)
    sites = [make_site(i, score=90 - i, email=f"owner{i}@shop{i}.example")
             for i in range(1, 9)]
    db.upsert_sites(sites)
    return db


@pytest.fixture
def no_send(monkeypatch):
    """Capture what would be sent instead of opening a socket.

    Honours dry_run exactly as the real sender does -- otherwise "a practice
    run sends nothing" would pass against a double that ignores the flag,
    which is the one property most worth actually proving.
    """
    sent = []

    def fake(config, identity, to_address, subject, body, dry_run=False, **kw):
        if dry_run:
            return ""
        sent.append(to_address)
        return f"<id{len(sent)}@x>"

    monkeypatch.setattr("machine_locator.outreach.sequences.send_email", fake)
    return sent


# ------------------------------------------------------------------ picking

def test_it_picks_the_best_first(stocked):
    picked = candidates(stocked, limit=3, min_score=0)
    assert [s.id for s in picked] == ["node/1", "node/2", "node/3"]


def test_it_skips_anyone_already_being_worked(stocked):
    stocked.update_pipeline("node/1", stage="interested")
    assert "node/1" not in [s.id for s in candidates(stocked, 5, 0)]


def test_it_skips_anyone_already_contacted(stocked, no_send):
    run(None, stocked, IDENTITY, SMTP, count=2, min_score=0)
    later = [s.id for s in candidates(stocked, 5, 0)]
    assert "node/1" not in later and "node/2" not in later


def test_it_never_picks_someone_who_opted_out(stocked):
    stocked.suppress("node/2", "site", "opted out")
    assert "node/2" not in [s.id for s in candidates(stocked, 8, 0)]


def test_score_floor_is_respected(stocked):
    assert candidates(stocked, 10, min_score=95) == []


# -------------------------------------------------------------------- plan

def test_plan_shows_the_real_email_before_anything_sends(stocked):
    result = plan(None, stocked, IDENTITY, SMTP, count=3, min_score=0)
    assert len(result.ready) == 3
    assert result.sample and "Business 1" in result.sample["subject"]
    assert "no cost to you" in result.sample["body"]
    # The opening pitch must not lead with a cut of the sales.
    assert "%" not in result.sample["body"]


def test_plan_separates_who_needs_a_lookup(db):
    install_builtins(db)
    db.upsert_sites([make_site(1, email="a@b.example"), make_site(2, email="")])
    result = plan(None, db, IDENTITY, SMTP, count=5, min_score=0)
    assert [r["site_id"] for r in result.ready] == ["node/1"]
    assert [r["site_id"] for r in result.need_lookup] == ["node/2"]


def test_plan_reports_the_gate_rather_than_pretending(db):
    install_builtins(db)
    db.upsert_sites([make_site(1, email="a@b.example")])
    naked = SenderIdentity.from_settings({"business_name": "X"})
    result = plan(None, db, naked, SmtpConfig(), count=5, min_score=0)
    assert result.blocked_reasons


def test_plan_knows_whether_you_have_ever_sent(stocked, no_send):
    assert plan(None, stocked, IDENTITY, SMTP, count=2, min_score=0).has_ever_sent is False
    run(None, stocked, IDENTITY, SMTP, count=1, min_score=0)
    assert plan(None, stocked, IDENTITY, SMTP, count=2, min_score=0).has_ever_sent is True


# --------------------------------------------------------------------- run

def test_a_full_run_picks_writes_and_sends(stocked, no_send):
    result = run(None, stocked, IDENTITY, SMTP, count=3, min_score=0)
    assert result.picked == 3
    assert result.enrolled == 3
    assert result.sent == 3          # only the day-0 note is due
    assert len(no_send) == 3
    assert stocked.get_pipeline("node/1")["stage"] == "contacted"
    # The later steps stay queued rather than all going at once.
    assert len(stocked.query_messages(status="queued", site_id="node/1")) == 2


def test_a_practice_run_sends_nothing(stocked, no_send):
    result = run(None, stocked, IDENTITY, SMTP, count=3, min_score=0, dry_run=True)
    assert result.enrolled == 3
    assert result.sent == 0
    assert no_send == []


def test_the_batch_is_sized_to_what_is_sendable_today(stocked, no_send):
    """Queueing more than the cap allows just means tomorrow's emails go out
    under today's assumptions."""
    stocked.set_setting("daily_send_cap", "2")
    result = run(None, stocked, IDENTITY, SMTP, count=20, min_score=0)
    assert result.picked == 2
    assert result.sent == 2


def test_a_used_up_cap_stops_it_cleanly(stocked, no_send):
    stocked.set_setting("daily_send_cap", "1")
    run(None, stocked, IDENTITY, SMTP, count=5, min_score=0)
    again = run(None, stocked, IDENTITY, SMTP, count=5, min_score=0)
    assert again.sent == 0
    assert "cap" in again.blocked_reasons[0].lower()


def test_it_refuses_to_run_without_the_compliance_details(stocked, no_send):
    """No postal address means no lawful commercial email, so autopilot must
    stop rather than route around the gate."""
    naked = SenderIdentity.from_settings({"business_name": "Blue Ox"})
    result = run(None, stocked, naked, SMTP, count=3, min_score=0)
    assert result.sent == 0 and result.blocked_reasons
    assert no_send == []


def test_prospects_with_no_findable_address_are_reported_not_invented(db, no_send, settings):
    install_builtins(db)
    db.upsert_sites([make_site(1, email=""), make_site(2, email="ok@shop.example")])

    # No website on file, so there is nothing to look up.
    result = run(settings, db, IDENTITY, SMTP, count=5, min_score=0)
    assert result.enrolled == 1
    assert any(s["reason"] == "No email address on file" for s in result.skipped)
    assert db.get_pipeline("node/1").get("contact_email") in (None, "")


def test_nothing_to_do_is_reported_plainly(db, no_send):
    install_builtins(db)
    result = run(None, db, IDENTITY, SMTP, count=5, min_score=0)
    assert result.picked == 0
    assert "No new prospects" in result.summary
