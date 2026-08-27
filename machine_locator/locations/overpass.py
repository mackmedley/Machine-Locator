"""OpenStreetMap Overpass client.

Overpass is free and needs no API key, which is why it is the default source.
The trade-offs it brings, and how this module handles them:

* Public instances are rate limited and occasionally down -> we rotate through
  mirrors and back off.
* A 60-filter query over a whole metro will time out -> filters are sent in
  batches and the results merged.
* Re-running a search should not re-hammer the API -> raw responses are cached
  on disk, keyed by the query text.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import requests

from ..config import OVERPASS_ENDPOINTS, Settings
from ..models import Site
from .categories import classify

ProgressFn = Callable[[str], None]


class OverpassError(RuntimeError):
    pass


def _filter_to_ql(filt: Dict[str, str]) -> str:
    return "".join(f'["{k}"="{v}"]' for k, v in filt.items())


def build_query(
    filters: Sequence[Dict[str, str]],
    bbox: Tuple[float, float, float, float],
    timeout: int = 180,
    area_name: Optional[str] = None,
) -> str:
    """Compose an Overpass QL union query.

    When ``area_name`` is given the search is clipped to that administrative
    boundary (strict city limits); otherwise the bounding box is used, which is
    both faster and what a route operator usually wants since delivery vans do
    not care about municipal lines.
    """
    header = f"[out:json][timeout:{timeout}];"
    if area_name:
        header += (
            f'area["boundary"="administrative"]["admin_level"="8"]'
            f'["name"="{area_name}"]->.searchArea;'
        )
        scope = "(area.searchArea)"
    else:
        south, west, north, east = bbox
        scope = f"({south},{west},{north},{east})"

    body = "".join(f"nwr{_filter_to_ql(f)}{scope};" for f in filters)
    return f"{header}({body});out center tags;"


class OverpassClient:
    def __init__(self, settings: Settings, endpoints: Sequence[str] = OVERPASS_ENDPOINTS):
        self.settings = settings
        self.endpoints = list(endpoints)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = settings.user_agent

    # ------------------------------------------------------------------ http

    def _cache_path(self, query: str) -> Path:
        digest = hashlib.sha1(query.encode("utf-8")).hexdigest()[:20]
        return self.settings.cache_dir / f"overpass_{digest}.json"

    def run(self, query: str, use_cache: bool = True, max_age_s: int = 7 * 86400) -> dict:
        self.settings.ensure_dirs()
        cache_file = self._cache_path(query)
        if use_cache and cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < max_age_s:
                try:
                    return json.loads(cache_file.read_text())
                except ValueError:
                    cache_file.unlink(missing_ok=True)

        last_error: Optional[Exception] = None
        for attempt, endpoint in enumerate(self.endpoints):
            try:
                response = self.session.post(
                    endpoint,
                    data={"data": query},
                    timeout=self.settings.request_timeout + self.settings.overpass_timeout,
                )
                if response.status_code in (429, 504):
                    # Overpass says "slow down"; honour it before the next mirror.
                    time.sleep(min(30, 5 * (attempt + 1)))
                    last_error = OverpassError(
                        f"{endpoint} returned {response.status_code} (rate limited)"
                    )
                    continue
                response.raise_for_status()
                payload = response.json()
                cache_file.write_text(json.dumps(payload))
                return payload
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                time.sleep(2 * (attempt + 1))
        raise OverpassError(
            f"every Overpass mirror failed; last error: {last_error}"
        )

    # --------------------------------------------------------------- fetching

    def fetch(
        self,
        filters: Sequence[Dict[str, str]],
        bbox: Tuple[float, float, float, float],
        area_name: Optional[str] = None,
        batch_size: int = 10,
        use_cache: bool = True,
        progress: Optional[ProgressFn] = None,
    ) -> List[dict]:
        """Fetch raw OSM elements for the given filters, batching the requests."""
        elements: Dict[str, dict] = {}
        batches = [
            list(filters[i : i + batch_size]) for i in range(0, len(filters), batch_size)
        ]
        for index, batch in enumerate(batches, start=1):
            if progress:
                labels = ", ".join(sorted({"=".join(next(iter(f.items()))) for f in batch}))
                progress(f"Overpass batch {index}/{len(batches)} ({labels})")
            query = build_query(batch, bbox, self.settings.overpass_timeout, area_name)
            payload = self.run(query, use_cache=use_cache)
            for element in payload.get("elements", []):
                key = f"{element.get('type')}/{element.get('id')}"
                elements[key] = element
            if index < len(batches):
                time.sleep(self.settings.rate_limit_seconds)
        return list(elements.values())


# ------------------------------------------------------------------ parsing


def element_coords(element: dict) -> Optional[Tuple[float, float]]:
    """Latitude/longitude for a node, or the centroid Overpass computed."""
    if "lat" in element and "lon" in element:
        return float(element["lat"]), float(element["lon"])
    center = element.get("center")
    if center and "lat" in center and "lon" in center:
        return float(center["lat"]), float(center["lon"])
    return None


def build_address(tags: Dict[str, str]) -> str:
    house = tags.get("addr:housenumber", "")
    street = tags.get("addr:street", "")
    line1 = " ".join(part for part in (house, street) if part).strip()
    city = tags.get("addr:city", "")
    state = tags.get("addr:state", "")
    postcode = tags.get("addr:postcode", "")
    tail = ", ".join(part for part in (city, f"{state} {postcode}".strip()) if part)
    return ", ".join(part for part in (line1, tail) if part)


def display_name(tags: Dict[str, str], fallback_label: str) -> str:
    for key in ("name", "operator", "brand", "official_name"):
        value = tags.get(key)
        if value:
            return value
    address = build_address(tags)
    if address:
        return f"{fallback_label} at {address}"
    return f"Unnamed {fallback_label.lower()}"


def elements_to_sites(elements: Iterable[dict]) -> List[Site]:
    """Turn raw OSM elements into classified Sites, dropping anything we
    cannot place or categorise."""
    sites: List[Site] = []
    for element in elements:
        tags = {str(k): str(v) for k, v in (element.get("tags") or {}).items()}
        if not tags:
            continue
        spec = classify(tags)
        if spec is None:
            continue
        coords = element_coords(element)
        if coords is None:
            continue
        lat, lon = coords
        sites.append(
            Site(
                id=f"{element.get('type')}/{element.get('id')}",
                name=display_name(tags, spec.label),
                category=spec.key,
                category_label=spec.label,
                lat=lat,
                lon=lon,
                address=build_address(tags),
                phone=tags.get("phone", "") or tags.get("contact:phone", ""),
                email=tags.get("email", "") or tags.get("contact:email", ""),
                website=tags.get("website", "") or tags.get("contact:website", ""),
                opening_hours=tags.get("opening_hours", ""),
                tags=tags,
            )
        )
    return sites


def elements_to_points(elements: Iterable[dict]) -> List[Tuple[float, float, str]]:
    """Coordinates only -- used for competition and existing-machine layers."""
    points: List[Tuple[float, float, str]] = []
    for element in elements:
        coords = element_coords(element)
        if coords is None:
            continue
        tags = element.get("tags") or {}
        label = tags.get("name") or tags.get("amenity") or tags.get("shop") or "?"
        points.append((coords[0], coords[1], str(label)))
    return points
