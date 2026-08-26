"""Source plug-in contract for listing aggregators."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List

from ..models import RouteListing
from .http import PoliteClient


@dataclass
class SourceReport:
    """What happened when we asked one source for listings.

    A source that returns nothing is not the same as a source that was blocked,
    and the CLI needs to tell the user which it was.
    """

    name: str
    label: str
    listings: List[RouteListing] = field(default_factory=list)
    error: str = ""
    skipped: str = ""
    pages_fetched: int = 0
    used_fallback: bool = False

    @property
    def ok(self) -> bool:
        return not self.error and not self.skipped

    @property
    def status(self) -> str:
        if self.skipped:
            return "skipped"
        if self.error:
            return "error"
        return "ok"


class Source(ABC):
    name: str = "source"
    label: str = "Source"
    enabled: bool = True
    notes: str = ""

    @abstractmethod
    def fetch(self, client: PoliteClient, limit: int = 100) -> SourceReport:
        """Return listings this source currently advertises."""

    def _report(self) -> SourceReport:
        return SourceReport(name=self.name, label=self.label)


def dedupe(listings: List[RouteListing]) -> List[RouteListing]:
    """Collapse the same ad syndicated across several sources.

    Brokers push identical copy to multiple portals, so match on the normalised
    title plus price as well as on our stable id.
    """
    seen_ids = set()
    seen_fingerprints = set()
    unique: List[RouteListing] = []
    for listing in listings:
        fingerprint = (
            " ".join(listing.title.lower().split())[:80],
            listing.price,
        )
        if listing.id in seen_ids or fingerprint in seen_fingerprints:
            continue
        seen_ids.add(listing.id)
        seen_fingerprints.add(fingerprint)
        unique.append(listing)
    return unique
