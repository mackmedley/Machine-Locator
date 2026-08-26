"""Runs every listing source, filters the noise, and stores the results."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from ..config import Settings
from ..db import Database
from ..models import RouteListing, utcnow
from .base import SourceReport, dedupe
from .html_source import build_listing, heuristic_extract
from .http import PoliteClient, RobotsDisallowed
from .registry import build_sources

ProgressFn = Callable[[str], None]


@dataclass
class RouteSearchResult:
    listings: List[RouteListing] = field(default_factory=list)
    new_listings: List[RouteListing] = field(default_factory=list)
    reports: List[SourceReport] = field(default_factory=list)
    started_at: str = ""

    @property
    def count(self) -> int:
        return len(self.listings)

    @property
    def blocked_sources(self) -> List[SourceReport]:
        return [r for r in self.reports if r.skipped]

    @property
    def failed_sources(self) -> List[SourceReport]:
        return [r for r in self.reports if r.error]


def make_client(settings: Settings, respect_robots: Optional[bool] = None) -> PoliteClient:
    return PoliteClient(
        user_agent=settings.user_agent,
        rate_limit_seconds=settings.rate_limit_seconds,
        timeout=settings.request_timeout,
        respect_robots=settings.respect_robots if respect_robots is None else respect_robots,
    )


def find_routes(
    settings: Settings,
    db: Database,
    sources: Optional[Sequence[str]] = None,
    config_path: Optional[Path] = None,
    min_relevance: float = 25.0,
    limit_per_source: int = 100,
    respect_robots: Optional[bool] = None,
    progress: Optional[ProgressFn] = None,
) -> RouteSearchResult:
    """Poll every enabled source and persist whatever clears ``min_relevance``."""
    started_at = utcnow()
    say: ProgressFn = progress or (lambda _msg: None)
    client = make_client(settings, respect_robots)
    active = build_sources(config_path, only=sources)

    if not active:
        raise ValueError("no sources selected -- check --source names against `mloc routes sources`")

    reports: List[SourceReport] = []
    everything: List[RouteListing] = []

    for source in active:
        say(f"Searching {source.label}...")
        try:
            report = source.fetch(client, limit=limit_per_source)
        except Exception as exc:  # a broken source must not sink the whole run
            report = SourceReport(name=source.name, label=source.label)
            report.error = f"{type(exc).__name__}: {exc}"

        kept = [l for l in report.listings if l.relevance >= min_relevance]
        dropped = len(report.listings) - len(kept)
        report.listings = kept
        reports.append(report)
        everything.extend(kept)

        if report.skipped:
            say(f"  skipped: {report.skipped}")
        elif report.error:
            say(f"  error: {report.error}")
        else:
            suffix = " (via fallback extractor)" if report.used_fallback else ""
            note = f" ({dropped} filtered out)" if dropped else ""
            say(f"  {len(kept)} relevant listing(s){note}{suffix}")

    unique = dedupe(everything)
    unique.sort(key=lambda l: (l.is_local, l.relevance), reverse=True)
    new_listings = db.upsert_listings(unique)

    db.record_run(
        "routes",
        started_at,
        found=len(unique),
        new_items=len(new_listings),
        notes="; ".join(f"{r.name}:{r.status}" for r in reports),
    )
    return RouteSearchResult(
        listings=unique,
        new_listings=new_listings,
        reports=reports,
        started_at=started_at,
    )


@dataclass
class Diagnosis:
    name: str
    label: str
    url: str
    status: str = ""
    robots: str = ""
    configured_matches: int = 0
    fallback_matches: int = 0
    sample_title: str = ""
    hint: str = ""


def diagnose(
    settings: Settings,
    sources: Optional[Sequence[str]] = None,
    config_path: Optional[Path] = None,
    respect_robots: Optional[bool] = None,
    save_html_to: Optional[Path] = None,
) -> List[Diagnosis]:
    """Check each source URL and report exactly why it does or does not work.

    This is the tool to reach for when a source suddenly returns nothing: it
    separates "the site blocked us", "the page loaded but our selector is
    stale", and "the page genuinely has no vending listings today".
    """
    from bs4 import BeautifulSoup

    client = make_client(settings, respect_robots)
    results: List[Diagnosis] = []

    for source in build_sources(config_path, only=sources, include_disabled=True):
        urls = source.urls() if hasattr(source, "urls") else list(
            getattr(source, "feed_urls", [])
        )
        for url in urls[:2]:  # first couple of URLs is enough to tell the story
            entry = Diagnosis(name=source.name, label=source.label, url=url)
            entry.robots = client.robots_reason(url)
            try:
                result = client.get(url)
                entry.status = f"HTTP {result.status}"
            except RobotsDisallowed as exc:
                entry.status = "blocked"
                entry.hint = str(exc)
                results.append(entry)
                continue
            except Exception as exc:
                entry.status = "error"
                entry.hint = f"{type(exc).__name__}: {exc}"
                results.append(entry)
                continue

            if save_html_to:
                save_html_to.mkdir(parents=True, exist_ok=True)
                (save_html_to / f"{source.name}.html").write_text(result.text)

            if getattr(source, "feed_urls", None) is not None:
                listings = source.parse_feed(result.text)  # type: ignore[attr-defined]
                entry.configured_matches = len(listings)
                if listings:
                    entry.sample_title = listings[0].title[:80]
                elif "<item" not in result.text and "<entry" not in result.text:
                    entry.hint = "response is not an RSS/Atom feed -- check the URL"
            else:
                soup = BeautifulSoup(result.text, "lxml")
                selector = getattr(source, "item_selector", "")
                if selector:
                    entry.configured_matches = len(soup.select(selector))
                fallback = heuristic_extract(soup, url, source.name, source.label)
                entry.fallback_matches = len(fallback)
                if fallback:
                    entry.sample_title = fallback[0].title[:80]
                if not entry.configured_matches and not entry.fallback_matches:
                    entry.hint = (
                        "page loaded but no vending listings found -- the results may be "
                        "rendered by JavaScript, or the search URL may have changed"
                    )
                elif selector and not entry.configured_matches:
                    entry.hint = (
                        f"item_selector '{selector}' matched nothing; "
                        f"the fallback extractor found {entry.fallback_matches}"
                    )
            results.append(entry)
    return results


# ------------------------------------------------------------------- import

IMPORT_ALIASES = {
    "title": ("title", "name", "business name", "headline", "listing"),
    "url": ("url", "link", "listing url", "web address"),
    "price": ("price", "asking price", "asking", "list price"),
    "cash_flow": ("cash flow", "cashflow", "sde", "net profit", "net income"),
    "gross_revenue": ("gross revenue", "revenue", "gross", "annual sales", "gross sales"),
    "location_text": ("location", "city", "area", "market", "county"),
    "description": ("description", "details", "summary", "notes", "body"),
    "posted_at": ("posted", "date", "posted at", "listed", "date listed"),
}


def _pick(row: Dict[str, str], field_name: str) -> str:
    lowered = {str(k).strip().lower(): (v or "") for k, v in row.items() if k}
    for alias in IMPORT_ALIASES[field_name]:
        if alias in lowered and lowered[alias]:
            return str(lowered[alias]).strip()
    return ""


def import_csv(
    db: Database, path: Path, source_name: str = "imported"
) -> List[RouteListing]:
    """Import listings from a CSV -- e.g. a broker's saved-search email export.

    Column names are matched loosely (``Asking Price``, ``price`` and
    ``List Price`` all work), so a file exported from a marketplace usually
    needs no editing. This is the escape hatch for sites that refuse
    automated access.
    """
    started_at = utcnow()
    listings: List[RouteListing] = []

    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"{path} has no header row")
        for row in reader:
            title = _pick(row, "title")
            if not title:
                continue
            description = _pick(row, "description")
            location_text = _pick(row, "location_text")
            listing = build_listing(
                source=source_name,
                title=title,
                url=_pick(row, "url"),
                description=description,
                price_text=_pick(row, "price"),
                location_text=location_text,
                cash_flow_text=_pick(row, "cash_flow"),
                posted_at=_pick(row, "posted_at"),
                blob=" ".join(str(v) for v in row.values() if v),
            )
            gross = _pick(row, "gross_revenue")
            if gross and listing.gross_revenue is None:
                from .filters import parse_money

                listing.gross_revenue = parse_money(gross) or parse_money(f"${gross}")
            listings.append(listing)

    unique = dedupe(listings)
    new_listings = db.upsert_listings(unique)
    db.record_run(
        "routes", started_at, found=len(unique), new_items=len(new_listings),
        notes=f"csv import from {path}",
    )
    return unique
