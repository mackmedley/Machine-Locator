"""Finding a prospect's contact details from the business's own website.

Hunting down an email address for four hundred businesses by hand is the step
that quietly kills this whole exercise, so it is worth automating properly.

What this does: for a prospect whose OpenStreetMap entry names a website, it
reads that site the way a customer would -- the homepage, and a contact or
about page if one is linked -- and takes the address the business publishes for
people to contact them on.

What it deliberately does not do: guess. An invented ``info@`` that bounces
costs far more than a blank field, because bounces are what mail providers use
to decide you are a spammer, and one bad run can poison a sending domain for
months. Every address here was actually printed on the business's own site.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .config import Settings
from .models import Site
from .routes.http import PoliteClient, RobotsDisallowed

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}")

# Addresses that are never a person who can say yes to a machine.
JUNK_LOCAL_PARTS = (
    "noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
    "postmaster", "abuse", "webmaster", "privacy", "legal", "unsubscribe",
)
JUNK_DOMAINS = (
    "example.com", "example.org", "sentry.io", "wixpress.com", "godaddy.com",
    "squarespace.com", "shopify.com", "wordpress.com", "sentry-next.wixpress.com",
    "domain.com", "yourdomain.com", "email.com",
)
# Image filenames and asset hashes match the email pattern surprisingly often.
FALSE_POSITIVE_RE = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js|woff2?)$", re.I)

# Ranked: who actually decides whether a machine goes in the lobby.
PREFERRED_LOCAL_PARTS = (
    "owner", "manager", "gm", "generalmanager", "office", "admin",
    "info", "contact", "hello", "sales", "frontdesk", "reception",
)
DEPRIORITISED_LOCAL_PARTS = ("careers", "jobs", "hr", "press", "media", "support", "help")

CONTACT_LINK_HINTS = ("contact", "about", "reach-us", "get-in-touch", "our-team", "staff")


@dataclass
class Findings:
    email: str = ""
    phone: str = ""
    emails: List[str] = field(default_factory=list)
    source_url: str = ""
    pages_read: int = 0
    problem: str = ""

    @property
    def found_anything(self) -> bool:
        return bool(self.email or self.phone)


def looks_like_a_real_address(address: str) -> bool:
    address = address.strip().lower()
    if not address or address.count("@") != 1:
        return False
    if FALSE_POSITIVE_RE.search(address):
        return False
    local, domain = address.split("@", 1)
    if any(junk in local for junk in JUNK_LOCAL_PARTS):
        return False
    if any(domain == junk or domain.endswith("." + junk) for junk in JUNK_DOMAINS):
        return False
    # Hex-looking local parts are almost always asset hashes, not people.
    if len(local) > 24 and re.fullmatch(r"[0-9a-f]+", local):
        return False
    return True


def rank_email(address: str, site_domain: str = "") -> int:
    """Higher is better. Sorts several found addresses into the useful one."""
    local, _, domain = address.lower().partition("@")
    score = 0
    # An address on the business's own domain beats a personal gmail scraped
    # from a footer credit.
    if site_domain and (domain == site_domain or domain.endswith("." + site_domain)):
        score += 40
    for index, preferred in enumerate(PREFERRED_LOCAL_PARTS):
        if local == preferred or local.startswith(preferred):
            score += 30 - index
            break
    if any(local.startswith(bad) for bad in DEPRIORITISED_LOCAL_PARTS):
        score -= 25
    if "." in local or "_" in local:
        score += 5   # firstname.lastname is usually a real person
    return score


def clean_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return ""
    return f"({digits[0:3]}) {digits[3:6]}-{digits[6:]}"


def harvest(html: str, page_url: str) -> Tuple[List[str], List[str], List[str]]:
    """(emails, phones, follow-up links) from one page."""
    soup = BeautifulSoup(html, "lxml")
    emails: List[str] = []
    phones: List[str] = []

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        low = href.lower()
        if low.startswith("mailto:"):
            # A mailto is the business saying "write to us here" -- the most
            # reliable signal on the page.
            address = low[7:].split("?")[0].strip()
            if looks_like_a_real_address(address):
                emails.append(address)
        elif low.startswith("tel:"):
            phone = clean_phone(href[4:])
            if phone:
                phones.append(phone)

    text = soup.get_text(" ", strip=True)
    for match in EMAIL_RE.findall(text):
        address = match.strip().lower().rstrip(".,;:")
        if looks_like_a_real_address(address):
            emails.append(address)
    for match in PHONE_RE.findall(text)[:12]:
        phone = clean_phone(match)
        if phone:
            phones.append(phone)

    follow: List[str] = []
    origin = urlparse(page_url).netloc
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        label = f"{href} {anchor.get_text(' ', strip=True)}".lower()
        if not any(hint in label for hint in CONTACT_LINK_HINTS):
            continue
        target = urljoin(page_url, href)
        if urlparse(target).netloc != origin:
            continue
        if target.rstrip("/") == page_url.rstrip("/"):
            continue
        if target not in follow:
            follow.append(target)
    return emails, phones, follow[:2]


def normalise_url(raw: str) -> str:
    url = (raw or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url.lstrip("/")
    parsed = urlparse(url)
    if not parsed.netloc or "." not in parsed.netloc:
        return ""
    return url


def find_contacts(client: PoliteClient, website: str, max_pages: int = 3) -> Findings:
    """Read a business's site and take the contact details it publishes."""
    url = normalise_url(website)
    if not url:
        return Findings(problem="No website on file")

    site_domain = urlparse(url).netloc.lower().lstrip("www.")
    seen: List[str] = []
    queue = [url]
    emails: List[str] = []
    phones: List[str] = []
    findings = Findings()

    while queue and findings.pages_read < max_pages:
        page_url = queue.pop(0)
        if page_url in seen:
            continue
        seen.append(page_url)
        try:
            result = client.get(page_url, retries=1)
        except RobotsDisallowed as exc:
            if not findings.pages_read:
                findings.problem = str(exc)
            break
        except Exception as exc:
            if not findings.pages_read:
                findings.problem = f"Could not read the site ({type(exc).__name__})"
            break

        findings.pages_read += 1
        page_emails, page_phones, follow = harvest(result.text, page_url)
        if page_emails and not findings.source_url:
            findings.source_url = page_url
        emails.extend(page_emails)
        phones.extend(page_phones)
        for link in follow:
            if link not in seen and link not in queue:
                queue.append(link)

    unique_emails = list(dict.fromkeys(emails))
    findings.emails = sorted(
        unique_emails, key=lambda a: rank_email(a, site_domain), reverse=True
    )
    if findings.emails:
        findings.email = findings.emails[0]
    if phones:
        findings.phone = list(dict.fromkeys(phones))[0]
    if not findings.found_anything and not findings.problem:
        findings.problem = "Site had no contact details on it"
    return findings


@dataclass
class EnrichResult:
    checked: int = 0
    emails_found: int = 0
    phones_found: int = 0
    no_website: int = 0
    nothing_found: int = 0
    details: List[Dict[str, str]] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.checked:
            return "Nothing needed looking up"
        return (f"{self.emails_found} email address(es) found across "
                f"{self.checked} business(es)")


def enrich_sites(
    settings: Settings,
    db,
    sites: Sequence[Site],
    respect_robots: Optional[bool] = None,
    progress=None,
) -> EnrichResult:
    """Fill in contact details for prospects that are missing them."""
    client = PoliteClient(
        user_agent=settings.user_agent,
        rate_limit_seconds=settings.rate_limit_seconds,
        timeout=settings.request_timeout,
        respect_robots=settings.respect_robots if respect_robots is None else respect_robots,
    )
    result = EnrichResult()

    for index, site in enumerate(sites, start=1):
        pipeline = db.get_pipeline(site.id)
        if pipeline.get("contact_email"):
            continue  # already has one; never overwrite what the user typed

        if progress:
            progress(f"Looking up {index} of {len(sites)}: {site.name}")

        website = site.website or (site.tags or {}).get("contact:website", "")
        if not normalise_url(website):
            result.no_website += 1
            # OSM sometimes carries a phone even with no website.
            phone = site.phone or (site.tags or {}).get("contact:phone", "")
            if phone and not pipeline.get("contact_phone"):
                db.update_pipeline(site.id, contact_phone=clean_phone(phone) or phone)
                result.phones_found += 1
            continue

        result.checked += 1
        findings = find_contacts(client, website)

        updates: Dict[str, str] = {}
        if findings.email:
            updates["contact_email"] = findings.email
            result.emails_found += 1
        phone = findings.phone or site.phone
        if phone and not pipeline.get("contact_phone"):
            updates["contact_phone"] = clean_phone(phone) or phone
            result.phones_found += 1

        if updates:
            db.update_pipeline(site.id, **updates)
            db.add_activity(
                site.id, "enriched", "Contact details found",
                f"{updates.get('contact_email', '')} {updates.get('contact_phone', '')}".strip(),
                {"source": findings.source_url or website},
            )
            result.details.append({
                "site": site.name,
                "email": findings.email,
                "source": findings.source_url or website,
            })
        else:
            result.nothing_found += 1

    return result
