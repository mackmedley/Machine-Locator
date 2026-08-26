"""A configuration-driven HTML listing scraper.

Selectors on broker sites change without warning, so this source is defined in
YAML (``sources.yaml``) rather than in code -- fixing a broken source means
editing one line, not writing a new class. When the configured selectors match
nothing, a heuristic extractor takes over so a layout change degrades into
slightly noisier results rather than into silence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..models import RouteListing
from .base import Source, SourceReport
from .filters import (
    parse_financials,
    parse_machine_count,
    parse_money,
    score_relevance,
)
from .http import PoliteClient, RobotsDisallowed

VENDING_HINT = re.compile(
    r"vending|snack route|soda route|beverage route|micro\s?market", re.IGNORECASE
)


@dataclass
class FieldSpec:
    """How to pull one field out of a listing card."""

    selectors: List[str] = field(default_factory=list)
    attr: str = "text"          # "text", or an attribute name such as "href"
    regex: Optional[str] = None  # optional capture applied to the extracted text

    @classmethod
    def parse(cls, raw: Any) -> "FieldSpec":
        if raw is None:
            return cls()
        if isinstance(raw, str):
            return cls(selectors=[raw])
        if isinstance(raw, list):
            return cls(selectors=[str(item) for item in raw])
        selector = raw.get("selector") or raw.get("selectors") or []
        if isinstance(selector, str):
            selector = [selector]
        return cls(
            selectors=[str(s) for s in selector],
            attr=str(raw.get("attr", "text")),
            regex=raw.get("regex"),
        )

    def extract(self, node: Any, base_url: str = "") -> str:
        for selector in self.selectors:
            found = node.select_one(selector) if selector else node
            if found is None:
                continue
            if self.attr == "text":
                value = " ".join(found.get_text(" ", strip=True).split())
            else:
                value = str(found.get(self.attr, "") or "")
                if self.attr == "href" and value:
                    value = urljoin(base_url, value)
            if not value:
                continue
            if self.regex:
                match = re.search(self.regex, value, re.IGNORECASE | re.DOTALL)
                if not match:
                    continue
                value = (match.group(1) if match.groups() else match.group(0)).strip()
            return value
        return ""


class HtmlSource(Source):
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.name = str(config["name"])
        self.label = str(config.get("label", self.name))
        self.enabled = bool(config.get("enabled", True))
        self.notes = str(config.get("notes", ""))
        self.url_templates: List[str] = [str(u) for u in config.get("urls", [])]
        self.pages = int(config.get("pages", 1))
        self.item_selector = str(config.get("item_selector", "") or "")
        self.allow_fallback = bool(config.get("fallback", True))
        fields = config.get("fields", {}) or {}
        self.fields = {key: FieldSpec.parse(value) for key, value in fields.items()}

    # ------------------------------------------------------------------ urls

    def urls(self) -> List[str]:
        out: List[str] = []
        for template in self.url_templates:
            if "{page}" in template:
                for page in range(1, self.pages + 1):
                    out.append(template.replace("{page}", str(page)))
            else:
                out.append(template)
        return out

    # --------------------------------------------------------------- fetching

    def fetch(self, client: PoliteClient, limit: int = 100) -> SourceReport:
        report = self._report()
        collected: List[RouteListing] = []

        for url in self.urls():
            if len(collected) >= limit:
                break
            try:
                result = client.get(url)
            except RobotsDisallowed as exc:
                report.skipped = str(exc)
                break
            except Exception as exc:  # network, parse, anything else
                report.error = f"{type(exc).__name__}: {exc}"
                break

            report.pages_fetched += 1
            soup = BeautifulSoup(result.text, "lxml")
            page_listings = self.parse_page(soup, url)
            if not page_listings and self.allow_fallback:
                page_listings = heuristic_extract(soup, url, self.name, self.label)
                if page_listings:
                    report.used_fallback = True
            if not page_listings:
                # Nothing on this page means later pages are unlikely to help.
                break
            collected.extend(page_listings)

        report.listings = collected[:limit]
        return report

    def parse_page(self, soup: BeautifulSoup, base_url: str) -> List[RouteListing]:
        if not self.item_selector:
            return []
        listings: List[RouteListing] = []
        for node in soup.select(self.item_selector):
            listing = self.parse_item(node, base_url)
            if listing is not None:
                listings.append(listing)
        return listings

    def parse_item(self, node: Any, base_url: str) -> Optional[RouteListing]:
        title = self.fields.get("title", FieldSpec()).extract(node, base_url)
        if not title:
            return None
        url = self.fields.get("url", FieldSpec(["a"], "href")).extract(node, base_url)
        description = self.fields.get("description", FieldSpec()).extract(node, base_url)
        price_text = self.fields.get("price", FieldSpec()).extract(node, base_url)
        location_text = self.fields.get("location", FieldSpec()).extract(node, base_url)
        cash_flow_text = self.fields.get("cash_flow", FieldSpec()).extract(node, base_url)
        posted_at = self.fields.get("posted_at", FieldSpec()).extract(node, base_url)

        blob = " ".join(
            part for part in (title, description, price_text, location_text) if part
        )
        return build_listing(
            source=self.name,
            title=title,
            url=url or base_url,
            description=description,
            price_text=price_text,
            location_text=location_text,
            cash_flow_text=cash_flow_text,
            posted_at=posted_at,
            blob=blob,
        )


def build_listing(
    source: str,
    title: str,
    url: str,
    description: str = "",
    price_text: str = "",
    location_text: str = "",
    cash_flow_text: str = "",
    posted_at: str = "",
    blob: str = "",
) -> RouteListing:
    """Assemble a RouteListing, filling the numeric fields from the ad copy."""
    text = blob or " ".join(p for p in (title, description, price_text, location_text) if p)
    financials = parse_financials(text)
    cash_flow = parse_money(cash_flow_text) or financials["cash_flow"]
    relevance, reasons, is_local = score_relevance(title, description, location_text or text)

    return RouteListing(
        id=RouteListing.make_id(source, url, title),
        source=source,
        title=title,
        url=url,
        price=parse_money(price_text) or parse_money(title),
        price_text=price_text,
        cash_flow=cash_flow,
        gross_revenue=financials["gross_revenue"],
        machine_count=parse_machine_count(text),
        location_text=location_text,
        state="OK" if is_local or " ok" in (location_text or "").lower() else "",
        description=description[:2000],
        posted_at=posted_at,
        relevance=round(relevance, 1),
        relevance_reasons=reasons,
        is_local=is_local,
    )


def heuristic_extract(
    soup: BeautifulSoup, base_url: str, source: str, label: str = ""
) -> List[RouteListing]:
    """Last-resort extractor for when the configured selectors stop matching.

    Walks every link on the page, keeps the ones whose text reads like a
    vending listing, and borrows surrounding text for price and location. Noisy
    by design -- the relevance filter downstream cleans it up.
    """
    listings: List[RouteListing] = []
    seen: set = set()
    origin = urlparse(base_url).netloc

    for anchor in soup.find_all("a", href=True):
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if not (12 <= len(title) <= 220):
            continue
        if not VENDING_HINT.search(title):
            continue

        href = urljoin(base_url, anchor["href"])
        if urlparse(href).netloc and origin and urlparse(href).netloc != origin:
            continue  # off-site link, almost always an ad
        if href in seen:
            continue
        seen.add(href)

        # Climb to a container that looks like a card, for price/location text.
        container = anchor
        for _ in range(4):
            if container.parent is None:
                break
            container = container.parent
            text = container.get_text(" ", strip=True)
            if len(text) > len(title) + 40:
                break
        context = " ".join(container.get_text(" ", strip=True).split())[:800]

        listings.append(
            build_listing(
                source=source,
                title=title,
                url=href,
                description=context,
                price_text=context,
                location_text=context,
                blob=f"{title} {context}",
            )
        )
    return listings
