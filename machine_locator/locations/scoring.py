"""Turn a raw site into a ranked prospect.

The score answers one question: *if I walked in tomorrow, how likely is this
to be a machine that pays for itself?* It blends the category priors from
``categories.py`` with what the map actually says about this specific address --
how many competing snack options sit within a short walk, whether somebody has
already put a machine there, how many other prospects are close enough to share
a service stop, and any size hints in the OSM tags.

Every component is 0-10 and the weights sum to 1.0, so the final number is a
plain 0-100 with an explanation attached.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from ..config import Settings
from ..geo import SpatialIndex
from ..models import Site
from .categories import BY_KEY, CategorySpec

WEIGHTS: Dict[str, float] = {
    "traffic": 0.24,
    "dwell": 0.22,
    "captivity": 0.16,
    "winnability": 0.10,
    "hours": 0.10,
    "access": 0.10,
    "route_density": 0.08,
}

# A machine already on site is close to disqualifying; one across the street is
# merely a warning.
MAX_SATURATION_PENALTY = 18.0

GRADES: Tuple[Tuple[float, str], ...] = (
    (85.0, "A+"),
    (75.0, "A"),
    (65.0, "B"),
    (50.0, "C"),
    (0.0, "D"),
)


def grade_for(score: float) -> str:
    for threshold, letter in GRADES:
        if score >= threshold:
            return letter
    return "D"


def hours_score(opening_hours: str) -> Tuple[float, str]:
    """How much of the day the machine can sell."""
    value = (opening_hours or "").strip().lower()
    if not value:
        return 6.0, ""
    if "24/7" in value:
        return 10.0, "open 24/7 -- sells on every shift"
    if re.search(r"mo-su|daily", value):
        return 8.0, "open seven days"
    if re.search(r"mo-sa|mo-fr", value):
        return 6.5, "weekday/business hours"
    return 6.0, ""


def size_bonus(tags: Dict[str, str], category: str) -> Tuple[float, str]:
    """Small traffic nudge when the tags say how big the place is.

    Returns a 0..1.5 bonus added to the traffic component.
    """
    def as_int(key: str) -> Optional[int]:
        raw = tags.get(key)
        if not raw:
            return None
        match = re.search(r"\d+", raw)
        return int(match.group()) if match else None

    rooms = as_int("rooms") or as_int("beds")
    if category == "hotel" and rooms:
        if rooms >= 120:
            return 1.5, f"{rooms} rooms -- large property"
        if rooms >= 60:
            return 0.8, f"{rooms} rooms"

    flats = as_int("building:flats")
    if category == "apartments" and flats:
        if flats >= 200:
            return 1.5, f"{flats} units -- supports multiple machines"
        if flats >= 100:
            return 1.0, f"{flats} units"
        if flats < 40:
            return -1.0, f"only {flats} units -- likely too small"

    levels = as_int("building:levels")
    if levels and levels >= 4:
        return min(1.5, 0.4 * levels - 1.0), f"{levels} storeys"

    capacity = as_int("capacity")
    if capacity and capacity >= 100:
        return 1.0, f"capacity {capacity}"
    return 0.0, ""


def density_score(neighbors: int) -> Tuple[float, str]:
    """Reward prospects that cluster -- a tight territory means less windshield
    time per machine, which is most of the cost of running a route."""
    if neighbors >= 25:
        return 10.0, f"{neighbors} other prospects within range -- dense territory"
    if neighbors >= 12:
        return 8.0, f"{neighbors} other prospects nearby"
    if neighbors >= 5:
        return 6.0, f"{neighbors} other prospects nearby"
    if neighbors >= 2:
        return 4.0, ""
    return 2.0, "isolated -- a stop here costs real drive time"


class SiteScorer:
    """Scores sites against competition and saturation layers."""

    def __init__(
        self,
        settings: Settings,
        competition_index: Optional[SpatialIndex] = None,
        vending_index: Optional[SpatialIndex] = None,
        site_index: Optional[SpatialIndex] = None,
    ):
        self.settings = settings
        self.competition_index = competition_index
        self.vending_index = vending_index
        self.site_index = site_index

    def score(self, site: Site) -> Site:
        spec: Optional[CategorySpec] = BY_KEY.get(site.category)
        if spec is None:
            site.score = 0.0
            site.grade = "D"
            site.reasons = ["unknown category"]
            return site

        reasons: List[str] = []
        if spec.note:
            reasons.append(spec.note)

        # --- traffic, with any size hint from the tags
        bonus, bonus_reason = size_bonus(site.tags, site.category)
        traffic = max(0.0, min(10.0, spec.traffic + bonus))
        if bonus_reason:
            reasons.append(bonus_reason)

        # --- captivity, degraded by every snack alternative within a walk
        competitors = 0
        if self.competition_index is not None:
            competitors = self.competition_index.count_within(
                site.lat, site.lon, self.settings.competition_radius_m
            )
        captivity = max(0.0, spec.captivity - min(6.0, competitors * 1.2))
        radius_label = f"{int(self.settings.competition_radius_m)}m"
        if competitors == 0:
            reasons.append(f"no store, cafe or drive-thru within {radius_label}")
        elif competitors >= 4:
            reasons.append(
                f"{competitors} competing food/drink options within {radius_label} "
                "-- people can just walk out"
            )
        else:
            reasons.append(f"{competitors} competing option(s) within {radius_label}")

        # --- saturation from machines already in the ground
        vending_nearby = 0
        if self.vending_index is not None:
            vending_nearby = self.vending_index.count_within(
                site.lat, site.lon, self.settings.saturation_radius_m
            )
        saturation_penalty = min(MAX_SATURATION_PENALTY, vending_nearby * 9.0)
        if vending_nearby:
            reasons.append(
                f"{vending_nearby} vending machine(s) already mapped on site -- "
                "the account may be taken"
            )

        # --- route density
        neighbors = 0
        if self.site_index is not None:
            # The index contains this site too, so discount it.
            neighbors = max(
                0,
                self.site_index.count_within(
                    site.lat, site.lon, self.settings.route_density_radius_m
                )
                - 1,
            )
        density, density_reason = density_score(neighbors)
        if density_reason:
            reasons.append(density_reason)

        # --- hours
        hours, hours_reason = hours_score(site.opening_hours)
        if hours_reason:
            reasons.append(hours_reason)

        # --- winnability is the inverse of how locked-up the category is
        winnability = 10.0 - spec.difficulty

        components = {
            "traffic": traffic,
            "dwell": spec.dwell,
            "captivity": captivity,
            "winnability": winnability,
            "hours": hours,
            "access": spec.access,
            "route_density": density,
        }
        raw = sum(WEIGHTS[key] * value for key, value in components.items()) * 10.0
        score = max(0.0, min(100.0, raw - saturation_penalty))

        site.score = round(score, 1)
        site.grade = grade_for(site.score)
        site.breakdown = {
            **{k: round(v, 2) for k, v in components.items()},
            "saturation_penalty": round(saturation_penalty, 2),
        }
        site.reasons = reasons
        site.competitors_nearby = competitors
        site.vending_nearby = vending_nearby
        site.neighbors_nearby = neighbors
        return site

    def score_all(self, sites: List[Site]) -> List[Site]:
        return [self.score(site) for site in sites]


def machine_recommendation(site: Site) -> str:
    """What to actually put in the box, from the category's ``fits``."""
    spec = BY_KEY.get(site.category)
    if not spec:
        return ""
    names = {
        "snack": "snack",
        "drink": "cold drink",
        "coffee": "coffee",
        "food": "fresh food / micro-market",
        "healthy": "healthy / Smart Snacks compliant",
        "sundry": "sundries & essentials",
    }
    return ", ".join(names.get(f, f) for f in spec.fits)
