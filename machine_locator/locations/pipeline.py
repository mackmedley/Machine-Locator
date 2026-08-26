"""Orchestrates the placement search end to end."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..config import Settings
from ..db import Database
from ..geo import SpatialIndex, kmeans, order_route, route_length_m
from ..models import Site, utcnow
from .categories import COMPETITION_FILTERS, VENDING_FILTERS, all_filters, BY_KEY
from .overpass import OverpassClient, elements_to_points, elements_to_sites

ProgressFn = Callable[[str], None]


@dataclass
class LocationSearchResult:
    sites: List[Site] = field(default_factory=list)
    new_sites: int = 0
    competitors: int = 0
    existing_machines: int = 0
    started_at: str = ""

    @property
    def count(self) -> int:
        return len(self.sites)


def find_locations(
    settings: Settings,
    db: Database,
    categories: Optional[Sequence[str]] = None,
    area_name: Optional[str] = None,
    use_cache: bool = True,
    territories: int = 0,
    progress: Optional[ProgressFn] = None,
) -> LocationSearchResult:
    """Pull candidate sites for the configured metro, score them, and store them.

    ``categories`` narrows the search to specific category keys (``gym``,
    ``laundromat``, ...). ``territories`` > 0 also splits the results into that
    many drivable service areas.
    """
    started_at = utcnow()
    say: ProgressFn = progress or (lambda _msg: None)
    client = OverpassClient(settings)

    if categories:
        unknown = [c for c in categories if c not in BY_KEY]
        if unknown:
            raise ValueError(
                f"unknown category: {', '.join(unknown)}. "
                f"Known: {', '.join(sorted(BY_KEY))}"
            )
        filters: List[Dict[str, str]] = []
        for key in categories:
            for filt in BY_KEY[key].filters:
                if filt not in filters:
                    filters.append(filt)
    else:
        filters = all_filters()

    say(f"Searching {settings.city} for {len(filters)} site types...")
    elements = client.fetch(
        filters, settings.bbox, area_name=area_name, use_cache=use_cache, progress=say
    )
    sites = elements_to_sites(elements)
    say(f"Found {len(sites)} candidate sites.")

    say("Mapping competition (stores, cafes, drive-thrus)...")
    competition_elements = client.fetch(
        list(COMPETITION_FILTERS), settings.bbox, area_name=area_name,
        use_cache=use_cache, progress=say,
    )
    competition_points = elements_to_points(competition_elements)

    say("Mapping vending machines already in the ground...")
    vending_elements = client.fetch(
        list(VENDING_FILTERS), settings.bbox, area_name=area_name,
        use_cache=use_cache, progress=say,
    )
    vending_points = elements_to_points(vending_elements)

    from .scoring import SiteScorer  # local import keeps the module graph flat

    scorer = SiteScorer(
        settings,
        competition_index=SpatialIndex(competition_points),
        vending_index=SpatialIndex(vending_points),
        site_index=SpatialIndex([(s.lat, s.lon, s.id) for s in sites]),
    )
    say(f"Scoring {len(sites)} sites...")
    scored = scorer.score_all(sites)
    scored.sort(key=lambda s: s.score, reverse=True)

    if territories > 0 and scored:
        assign_territories(scored, territories)

    new_count = db.upsert_sites(scored)
    db.record_run(
        "locations",
        started_at,
        found=len(scored),
        new_items=new_count,
        notes=f"{len(competition_points)} competitors, {len(vending_points)} machines mapped",
    )
    return LocationSearchResult(
        sites=scored,
        new_sites=new_count,
        competitors=len(competition_points),
        existing_machines=len(vending_points),
        started_at=started_at,
    )


def assign_territories(sites: List[Site], count: int) -> Dict[str, int]:
    """Group sites into ``count`` geographic service territories."""
    points = [(s.lat, s.lon) for s in sites]
    labels = kmeans(points, count)
    assignments: Dict[str, int] = {}
    for site, label in zip(sites, labels):
        site.territory = int(label)
        assignments[site.id] = int(label)
    return assignments


@dataclass
class ServiceRoute:
    """An ordered restocking run through a set of sites."""

    stops: List[Site]
    distance_m: float

    @property
    def distance_mi(self) -> float:
        return self.distance_m / 1609.344


def plan_service_route(
    sites: Sequence[Site], start: Optional[Tuple[float, float]] = None
) -> ServiceRoute:
    """Order stops into an efficient loop for a restocking run.

    If ``start`` is given (your warehouse), it is inserted as the first stop and
    the tour is rotated to begin there.
    """
    if not sites:
        return ServiceRoute([], 0.0)

    points: List[Tuple[float, float]] = [(s.lat, s.lon) for s in sites]
    anchor_index = 0
    if start is not None:
        points.insert(0, start)
        anchor_index = 0

    order = order_route(points, start=anchor_index)
    distance = route_length_m(points, order)

    if start is not None:
        ordered = [sites[i - 1] for i in order if i != 0]
    else:
        ordered = [sites[i] for i in order]
    return ServiceRoute(ordered, distance)
