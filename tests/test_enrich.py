from pathlib import Path

import pytest

from machine_locator.enrich import (
    clean_phone, enrich_sites, find_contacts, harvest,
    looks_like_a_real_address, normalise_url, rank_email,
)
from machine_locator.models import Site
from machine_locator.routes.http import FetchResult, PoliteClient, RobotsDisallowed

FIXTURES = Path(__file__).parent / "fixtures"


def make_site(site_id="node/1", website="https://northlinelaundry.com", phone=""):
    return Site(
        id=site_id, name="Northline Coin Laundry", category="laundromat",
        category_label="Laundromat", lat=35.4676, lon=-97.5164,
        address="4180 N Portland Ave", score=81.0, grade="A",
        website=website, phone=phone,
    )


@pytest.fixture
def fake_web(monkeypatch):
    """Serve the two fixture pages for one business's site."""
    def fake_get(self, url, retries=2):
        if "blocked" in url:
            raise RobotsDisallowed("robots.txt disallows it")
        if "contact" in url or "about" in url:
            return FetchResult(url=url, status=200,
                               text=(FIXTURES / "business_contact_page.html").read_text())
        return FetchResult(url=url, status=200,
                           text=(FIXTURES / "business_site.html").read_text())

    monkeypatch.setattr(PoliteClient, "get", fake_get)
    monkeypatch.setattr(PoliteClient, "allowed", lambda self, url: "blocked" not in url)


def client():
    return PoliteClient(user_agent="test", rate_limit_seconds=0.0)


# ---------------------------------------------------------------- filtering

@pytest.mark.parametrize("address", [
    "manager@northlinelaundry.com", "info@shop.co", "jane.doe@firm.com",
])
def test_real_addresses_pass(address):
    assert looks_like_a_real_address(address)


@pytest.mark.parametrize("address", [
    "noreply@x.com", "no-reply@x.com", "postmaster@x.com",
    "someone@example.com", "hello@wixpress.com",
    "logo@2x.png", "sprite@icons.svg", "",
    "notanemail", "a@b@c.com",
])
def test_junk_addresses_are_rejected(address):
    assert not looks_like_a_real_address(address)


def test_asset_hashes_are_not_people():
    assert not looks_like_a_real_address("a1b2c3d4e5f6a7b8c9d0e1f2a3@cdn.net")


# ------------------------------------------------------------------ ranking

def test_the_decision_maker_outranks_the_careers_inbox():
    domain = "northlinelaundry.com"
    assert (rank_email("manager@northlinelaundry.com", domain)
            > rank_email("careers@northlinelaundry.com", domain))


def test_own_domain_beats_a_web_designer_credit():
    domain = "northlinelaundry.com"
    assert (rank_email("info@northlinelaundry.com", domain)
            > rank_email("studio@webshop.example", domain))


# -------------------------------------------------------------------- phone

@pytest.mark.parametrize("raw,expected", [
    ("+1 405 555 0134", "(405) 555-0134"),
    ("4055550134", "(405) 555-0134"),
    ("(405) 555-0134", "(405) 555-0134"),
    ("405.555.0134", "(405) 555-0134"),
])
def test_phone_normalising(raw, expected):
    assert clean_phone(raw) == expected


@pytest.mark.parametrize("raw", ["12345", "", "not a phone", "1234567890123"])
def test_bad_phones_rejected(raw):
    assert clean_phone(raw) == ""


# ------------------------------------------------------------------- urls

def test_url_normalising():
    assert normalise_url("northline.com") == "https://northline.com"
    assert normalise_url("http://x.com/a") == "http://x.com/a"
    assert normalise_url("") == ""
    assert normalise_url("not a url") == ""


# ---------------------------------------------------------------- harvest

def test_harvest_finds_contacts_and_follow_links():
    html = (FIXTURES / "business_site.html").read_text()
    emails, phones, follow = harvest(html, "https://northlinelaundry.com/")
    assert "careers@northlinelaundry.com" in emails
    assert "noreply@northlinelaundry.com" not in emails   # filtered
    assert "logo@2x.png" not in emails                     # image, not an address
    assert "(405) 555-0134" in phones
    assert any("contact" in link for link in follow)


# ------------------------------------------------------------------ lookup

def test_follows_the_contact_page_and_picks_the_manager(fake_web):
    findings = find_contacts(client(), "https://northlinelaundry.com")
    assert findings.email == "manager@northlinelaundry.com"
    assert findings.phone == "(405) 555-0134"
    assert findings.pages_read >= 2
    assert findings.source_url


def test_no_website_is_reported_not_guessed(fake_web):
    findings = find_contacts(client(), "")
    assert findings.email == ""
    assert findings.problem == "No website on file"


def test_robots_refusal_is_reported(fake_web):
    findings = find_contacts(client(), "https://blocked.example")
    assert findings.email == ""
    assert findings.problem


# ------------------------------------------------------------------ end to end

def test_enrich_fills_the_pipeline(fake_web, settings, db):
    db.upsert_sites([make_site()])
    result = enrich_sites(settings, db, [make_site()])
    assert result.emails_found == 1
    pipeline = db.get_pipeline("node/1")
    assert pipeline["contact_email"] == "manager@northlinelaundry.com"
    assert pipeline["contact_phone"] == "(405) 555-0134"
    assert db.site_activities("node/1")[0]["kind"] == "enriched"


def test_it_never_overwrites_an_address_you_typed(fake_web, settings, db):
    db.upsert_sites([make_site()])
    db.update_pipeline("node/1", contact_email="owner.i.met@northline.com")
    result = enrich_sites(settings, db, [make_site()])
    assert result.checked == 0
    assert db.get_pipeline("node/1")["contact_email"] == "owner.i.met@northline.com"


def test_a_site_with_no_website_still_gets_its_mapped_phone(fake_web, settings, db):
    site = make_site("node/2", website="", phone="405-555-9999")
    db.upsert_sites([site])
    result = enrich_sites(settings, db, [site])
    assert result.no_website == 1
    assert db.get_pipeline("node/2")["contact_phone"] == "(405) 555-9999"


def test_nothing_is_invented_when_the_site_gives_nothing(fake_web, settings, db, monkeypatch):
    monkeypatch.setattr(
        PoliteClient, "get",
        lambda self, url, retries=2: FetchResult(url=url, status=200,
                                                 text="<html><body>Hi</body></html>"),
    )
    site = make_site("node/3")
    db.upsert_sites([site])
    result = enrich_sites(settings, db, [site])
    assert result.emails_found == 0
    assert result.nothing_found == 1
    # An invented address that bounces is worse than a blank field.
    assert db.get_pipeline("node/3").get("contact_email") in (None, "")
