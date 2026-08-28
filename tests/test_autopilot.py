import pytest

from machine_locator.autopilot import candidates, collect_sendable, plan, run
from machine_locator.enrich import EnrichResult
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


def make_site(n, score=80.0, email="", name=None, website=""):
    return Site(
        id=f"node/{n}", name=name or f"Business {n}", category="laundromat",
        category_label="Laundromat", lat=35.4676 + n / 1000, lon=-97.5164,
        address=f"{n} Main St, Oklahoma City, OK", score=score, grade="A",
        email=email, website=website,
        reasons=["no store, cafe or drive-thru within 400m"],
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


@pytest.fixture
def fake_lookup(monkeypatch):
    """Stand in for reading a business website, recording what got read.

    A site whose website says ``found`` publishes an address; the rest do not.
    Nothing opens a socket, so the test is about the choosing, not the parsing.
    """
    read = []

    def fake(settings, db, sites, respect_robots=None, progress=None):
        result = EnrichResult()
        for site in sites:
            read.append(site.id)
            result.checked += 1
            if "found" in (site.website or ""):
                db.update_pipeline(
                    site.id, contact_email=f"found{len(read)}@shop.example")
                result.emails_found += 1
            else:
                result.nothing_found += 1
        return result

    monkeypatch.setattr("machine_locator.enrich.enrich_sites", fake)
    return read


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
    """Three groups, because they need three different things: send now, read
    a website first, or pick up the phone."""
    install_builtins(db)
    db.upsert_sites([
        make_site(1, email="a@b.example"),
        make_site(2, email="", website="https://shop2.example"),
        make_site(3, email=""),
    ])
    result = plan(None, db, IDENTITY, SMTP, count=5, min_score=0)
    assert [r["site_id"] for r in result.ready] == ["node/1"]
    assert [r["site_id"] for r in result.need_lookup] == ["node/2"]
    assert [r["site_id"] for r in result.no_contact] == ["node/3"]


def test_plan_stops_counting_lookups_once_the_batch_is_full(db):
    """If the top of the list already has enough addresses, nothing needs
    reading -- the dialog should not offer to go looking anyway."""
    install_builtins(db)
    db.upsert_sites([make_site(i, score=90 - i, email=f"o{i}@s{i}.example")
                     for i in range(1, 6)]
                    + [make_site(9, score=10, website="https://late.example")])
    result = plan(None, db, IDENTITY, SMTP, count=2, min_score=0)
    assert len(result.ready) == 2
    assert result.need_lookup == []
    assert result.will_write_to == 2


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
    assert any(s["reason"] == "No website to look at" for s in result.skipped)
    assert result.looked_up == 0
    assert db.get_pipeline("node/1").get("contact_email") in (None, "")


def test_nothing_to_do_is_reported_plainly(db, no_send):
    install_builtins(db)
    result = run(None, db, IDENTITY, SMTP, count=5, min_score=0)
    assert result.picked == 0
    assert "No new prospects" in result.summary


# ------------------------------------------------- filling the batch

def test_it_fills_the_batch_with_businesses_it_can_email(db, no_send):
    """Asking for three means three emails, not three attempts.

    The top of the list has nowhere to write to, so the run has to work past
    it rather than send a short batch -- that is the whole point of the button.
    """
    install_builtins(db)
    db.upsert_sites(
        [make_site(i, score=90 - i) for i in (1, 2, 3)]
        + [make_site(i, score=90 - i, email=f"owner{i}@shop.example")
           for i in (4, 5, 6, 7)])

    result = run(None, db, IDENTITY, SMTP, count=3, min_score=0)

    assert result.picked == 3
    assert result.enrolled == 3
    assert no_send == ["owner4@shop.example", "owner5@shop.example",
                       "owner6@shop.example"]
    # The ones it stepped over are reported, so they can be phoned instead.
    assert len(result.skipped) == 3
    assert all(s["reason"] == "No website to look at" for s in result.skipped)
    assert result.looked_up == 0


def test_it_keeps_the_ranking_while_filling(db, no_send):
    """Stepping over a blank does not reshuffle the list: the best reachable
    business is still the first one written to."""
    install_builtins(db)
    db.upsert_sites([
        make_site(1, score=99),                                   # unreachable
        make_site(2, score=98, email="best@shop.example"),
        make_site(3, score=10, email="worst@shop.example"),
    ])
    run(None, db, IDENTITY, SMTP, count=2, min_score=0)
    assert no_send == ["best@shop.example", "worst@shop.example"]


def test_it_reads_websites_until_the_batch_is_full_then_stops(db, fake_lookup):
    """Lookups are the slow part, so it buys only as many as it needs."""
    install_builtins(db)
    db.upsert_sites([
        make_site(1, score=99, website="https://blank1.example"),
        make_site(2, score=98, website="https://blank2.example"),
        make_site(3, score=97, website="https://found3.example"),
        make_site(4, score=96, website="https://found4.example"),
        make_site(5, score=95, website="https://found5.example"),
        make_site(6, score=94, website="https://found6.example"),
    ])

    gathered = collect_sendable(None, db, target=2, min_score=0)

    assert [s.id for s in gathered["sites"]] == ["node/3", "node/4"]
    assert gathered["contacts_found"] == 2
    # It never paid for the two it did not need.
    assert "node/5" not in fake_lookup and "node/6" not in fake_lookup
    # And the two blanks are reported with the reason that fits them now.
    assert [s["reason"] for s in gathered["passed_over"]] == [
        "No email published on their website"] * 2


def test_a_full_list_costs_no_lookups(db, fake_lookup):
    install_builtins(db)
    db.upsert_sites([make_site(i, score=90 - i, email=f"o{i}@shop.example",
                               website="https://found.example")
                     for i in range(1, 5)])
    gathered = collect_sendable(None, db, target=3, min_score=0)
    assert len(gathered["sites"]) == 3
    assert gathered["lookups"] == 0 and fake_lookup == []


def test_a_lookup_that_finds_nothing_is_never_guessed_at(db, fake_lookup):
    """A found address gets used; an unfound one stays empty rather than
    becoming an invented info@ that bounces."""
    install_builtins(db)
    db.upsert_sites([make_site(1, website="https://blank.example")])
    gathered = collect_sendable(None, db, target=1, min_score=0)
    assert gathered["sites"] == []
    assert db.get_pipeline("node/1").get("contact_email") in (None, "")


def test_a_bad_address_is_a_final_answer_not_a_blank_to_chase(db, fake_lookup):
    """Junk on file is a stated reason, so it is reported as that rather than
    costing a website read and then being reported as "no email found"."""
    install_builtins(db)
    db.upsert_sites([make_site(1, email="not-an-email",
                               website="https://found.example"),
                     make_site(2, score=70, email="ok@shop.example")])

    gathered = collect_sendable(None, db, target=2, min_score=0)

    assert [s.id for s in gathered["sites"]] == ["node/2"]
    assert fake_lookup == []
    assert "not a valid email" in gathered["passed_over"][0]["reason"]


def test_a_discovered_address_still_goes_through_the_opt_out_list(db, fake_lookup):
    """Finding an address is not permission to use it -- enrichment must not
    become the way round the do-not-contact list."""
    install_builtins(db)
    db.upsert_sites([make_site(1, website="https://found.example"),
                     make_site(2, score=70, email="ok@shop.example")])
    db.suppress("found1@shop.example", "email", "asked to stop")  # what it finds

    gathered = collect_sendable(None, db, target=2, min_score=0)

    assert [s.id for s in gathered["sites"]] == ["node/2"]
    assert fake_lookup == ["node/1"]
    assert "do-not-contact" in gathered["passed_over"][0]["reason"].lower()
