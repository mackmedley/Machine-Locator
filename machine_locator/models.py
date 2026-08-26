"""Core data types shared by both halves of the app."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Site:
    """A candidate host site for a vending machine."""

    id: str                       # e.g. "node/12345678"
    name: str
    category: str                 # our internal category key, e.g. "gym"
    category_label: str
    lat: float
    lon: float
    address: str = ""
    phone: str = ""
    website: str = ""
    opening_hours: str = ""
    tags: Dict[str, str] = field(default_factory=dict)

    # Populated by the scorer
    score: float = 0.0
    grade: str = ""
    breakdown: Dict[str, float] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)
    competitors_nearby: int = 0
    vending_nearby: int = 0
    neighbors_nearby: int = 0
    territory: Optional[int] = None

    source: str = "openstreetmap"
    first_seen: str = field(default_factory=utcnow)
    last_seen: str = field(default_factory=utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_geojson_feature(self) -> Dict[str, Any]:
        props = self.to_dict()
        props.pop("lat", None)
        props.pop("lon", None)
        return {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [self.lon, self.lat]},
            "properties": props,
        }


@dataclass
class RouteListing:
    """A vending machine route / vending business advertised for sale."""

    id: str
    source: str
    title: str
    url: str
    price: Optional[float] = None
    price_text: str = ""
    cash_flow: Optional[float] = None
    gross_revenue: Optional[float] = None
    machine_count: Optional[int] = None
    location_text: str = ""
    state: str = ""
    description: str = ""
    posted_at: str = ""
    relevance: float = 0.0
    relevance_reasons: List[str] = field(default_factory=list)
    is_local: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)
    first_seen: str = field(default_factory=utcnow)
    last_seen: str = field(default_factory=utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def make_id(source: str, url: str, title: str) -> str:
        """Stable id so the same ad seen next week is not a new row.

        URLs pick up tracking query strings, so fall back to the title when the
        URL is missing and always hash source+url+title together.
        """
        basis = f"{source}|{url.strip().lower()}|{title.strip().lower()}"
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
