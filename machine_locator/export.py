"""Export sites and listings to formats you can actually use in the field.

CSV goes into a spreadsheet or a CRM; GeoJSON drops straight onto Google
My Maps or QGIS so a driver can see the prospects as pins.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from .locations.scoring import machine_recommendation
from .models import RouteListing, Site

SITE_COLUMNS = (
    "score", "grade", "name", "category_label", "address", "phone", "website",
    "opening_hours", "lat", "lon", "territory", "competitors_nearby",
    "vending_nearby", "neighbors_nearby", "sell_here", "why", "osm_id", "map_url",
)

LISTING_COLUMNS = (
    "relevance", "title", "price", "cash_flow", "gross_revenue", "machine_count",
    "location_text", "is_local", "source", "posted_at", "first_seen", "url",
    "why", "description",
)


def _site_row(site: Site) -> Dict[str, Any]:
    return {
        "score": site.score,
        "grade": site.grade,
        "name": site.name,
        "category_label": site.category_label,
        "address": site.address,
        "phone": site.phone,
        "website": site.website,
        "opening_hours": site.opening_hours,
        "lat": round(site.lat, 6),
        "lon": round(site.lon, 6),
        "territory": "" if site.territory is None else site.territory,
        "competitors_nearby": site.competitors_nearby,
        "vending_nearby": site.vending_nearby,
        "neighbors_nearby": site.neighbors_nearby,
        "sell_here": machine_recommendation(site),
        "why": " | ".join(site.reasons),
        "osm_id": site.id,
        "map_url": f"https://www.google.com/maps/search/?api=1&query={site.lat},{site.lon}",
    }


def _listing_row(listing: RouteListing) -> Dict[str, Any]:
    return {
        "relevance": listing.relevance,
        "title": listing.title,
        "price": listing.price if listing.price is not None else "",
        "cash_flow": listing.cash_flow if listing.cash_flow is not None else "",
        "gross_revenue": listing.gross_revenue if listing.gross_revenue is not None else "",
        "machine_count": listing.machine_count if listing.machine_count is not None else "",
        "location_text": listing.location_text,
        "is_local": "yes" if listing.is_local else "no",
        "source": listing.source,
        "posted_at": listing.posted_at,
        "first_seen": listing.first_seen,
        "url": listing.url,
        "why": " | ".join(listing.relevance_reasons),
        "description": (listing.description or "")[:500],
    }


def _write_csv(path: Path, columns: Sequence[str], rows: Iterable[Dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def sites_to_csv(sites: Sequence[Site], path: Path) -> Path:
    return _write_csv(path, SITE_COLUMNS, (_site_row(s) for s in sites))


def listings_to_csv(listings: Sequence[RouteListing], path: Path) -> Path:
    return _write_csv(path, LISTING_COLUMNS, (_listing_row(l) for l in listings))


def sites_to_geojson(sites: Sequence[Site], path: Path) -> Path:
    """GeoJSON with a `marker-color` per grade so it renders sensibly in
    Google My Maps, QGIS and geojson.io without any styling work."""
    colors = {"A+": "#1a9850", "A": "#66bd63", "B": "#fee08b", "C": "#fdae61", "D": "#d73027"}
    features: List[Dict[str, Any]] = []
    for site in sites:
        feature = site.to_geojson_feature()
        feature["properties"] = {
            **_site_row(site),
            "marker-color": colors.get(site.grade, "#888888"),
            "marker-symbol": "grocery",
        }
        features.append(feature)
    payload = {"type": "FeatureCollection", "features": features}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return path


def sites_to_json(sites: Sequence[Site], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([s.to_dict() for s in sites], indent=2))
    return path


def listings_to_json(listings: Sequence[RouteListing], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([l.to_dict() for l in listings], indent=2))
    return path
