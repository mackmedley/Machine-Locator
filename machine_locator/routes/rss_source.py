"""RSS/Atom listing source.

Several classifieds still publish machine-readable feeds, which are cheaper and
politer to poll than scraping HTML, and they do not break when a designer
changes a CSS class.
"""

from __future__ import annotations

from typing import Any, Dict, List

from bs4 import BeautifulSoup

from ..models import RouteListing
from .base import Source, SourceReport
from .html_source import build_listing
from .http import PoliteClient, RobotsDisallowed


def _text(node: Any, *names: str) -> str:
    for name in names:
        found = node.find(name)
        if found is not None and found.get_text(strip=True):
            return " ".join(found.get_text(" ", strip=True).split())
    return ""


def _link(node: Any) -> str:
    found = node.find("link")
    if found is None:
        return ""
    # RSS puts the URL in the element text; Atom puts it in an href attribute.
    href = found.get("href") if found.has_attr("href") else None
    return (href or found.get_text(strip=True) or "").strip()


class RssSource(Source):
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.name = str(config["name"])
        self.label = str(config.get("label", self.name))
        self.enabled = bool(config.get("enabled", True))
        self.notes = str(config.get("notes", ""))
        self.feed_urls: List[str] = [str(u) for u in config.get("urls", [])]
        self.default_location = str(config.get("default_location", ""))

    def fetch(self, client: PoliteClient, limit: int = 100) -> SourceReport:
        report = self._report()
        collected: List[RouteListing] = []

        for url in self.feed_urls:
            if len(collected) >= limit:
                break
            try:
                result = client.get(url)
            except RobotsDisallowed as exc:
                report.skipped = str(exc)
                break
            except Exception as exc:
                report.error = f"{type(exc).__name__}: {exc}"
                break

            report.pages_fetched += 1
            collected.extend(self.parse_feed(result.text))

        report.listings = collected[:limit]
        return report

    def parse_feed(self, xml_text: str) -> List[RouteListing]:
        soup = BeautifulSoup(xml_text, "xml")
        items = soup.find_all("item") or soup.find_all("entry")
        listings: List[RouteListing] = []
        for item in items:
            title = _text(item, "title")
            if not title:
                continue
            description = _text(item, "description", "summary", "content")
            location = self.default_location or _text(item, "category")
            posted_at = _text(item, "pubDate", "date", "updated", "published")
            listings.append(
                build_listing(
                    source=self.name,
                    title=title,
                    url=_link(item),
                    description=description,
                    price_text=f"{title} {description}",
                    location_text=location or f"{title} {description}",
                    posted_at=posted_at,
                    blob=f"{title} {description} {location}",
                )
            )
        return listings
