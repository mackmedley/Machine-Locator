import re

import pytest

from machine_locator.locations.overpass import (
    OverpassClient, build_address, build_query, display_name,
    element_coords, elements_to_points, elements_to_sites,
)
from machine_locator.locations.pipeline import find_locations, plan_service_route

BBOX = (35.24, -97.90, 35.75, -97.10)


def test_build_query_uses_bbox_by_default():
    query = build_query([{"shop": "laundry"}], BBOX, timeout=90)
    assert query.startswith("[out:json][timeout:90];")
    assert 'nwr["shop"="laundry"](35.24,-97.9,35.75,-97.1);' in query
    assert query.endswith("out center tags;")
    assert "area" not in query


def test_build_query_can_clip_to_city_limits():
    query = build_query([{"shop": "laundry"}], BBOX, area_name="Oklahoma City")
    assert '["name"="Oklahoma City"]' in query
    assert '["admin_level"="8"]' in query
    assert "(area.searchArea)" in query


def test_build_query_handles_multi_tag_filters():
    query = build_query([{"building": "residential", "residential": "apartments"}], BBOX)
    assert '["building"="residential"]["residential"="apartments"]' in query


def test_element_coords_handles_nodes_and_ways():
    assert element_coords({"lat": 35.0, "lon": -97.0}) == (35.0, -97.0)
    assert element_coords({"center": {"lat": 35.1, "lon": -97.1}}) == (35.1, -97.1)
    assert element_coords({"type": "way", "id": 1}) is None


def test_build_address_and_display_name():
    tags = {
        "addr:housenumber": "123", "addr:street": "NW 23rd St",
        "addr:city": "Oklahoma City", "addr:state": "OK", "addr:postcode": "73106",
    }
    assert build_address(tags) == "123 NW 23rd St, Oklahoma City, OK 73106"
    assert display_name({**tags, "name": "Suds"}, "Laundromat") == "Suds"
    assert display_name(tags, "Laundromat").startswith("Laundromat at 123")
    assert display_name({}, "Laundromat") == "Unnamed laundromat"


def test_display_name_falls_back_to_operator():
    assert display_name({"operator": "Speed Queen"}, "Laundromat") == "Speed Queen"


def test_elements_to_sites_drops_unusable_elements():
    elements = [
        {"type": "node", "id": 1, "lat": 35.4, "lon": -97.5, "tags": {"shop": "laundry"}},
        {"type": "node", "id": 2, "lat": 35.4, "lon": -97.5, "tags": {"amenity": "bench"}},
        {"type": "way", "id": 3, "tags": {"shop": "laundry"}},          # no coordinates
        {"type": "node", "id": 4, "lat": 35.4, "lon": -97.5},           # no tags
    ]
    sites = elements_to_sites(elements)
    assert [s.id for s in sites] == ["node/1"]
    assert sites[0].category == "laundromat"


def test_elements_to_points_keeps_only_locatable():
    points = elements_to_points([
        {"type": "node", "id": 1, "lat": 35.4, "lon": -97.5, "tags": {"shop": "convenience"}},
        {"type": "way", "id": 2, "tags": {"shop": "convenience"}},
    ])
    assert len(points) == 1


# ------------------------------------------------------------------ pipeline

# A tiny fake OSM world: two prospects, a competitor next door to the gym, and
# a machine already installed there.
FAKE_WORLD = [
    {"type": "node", "id": 1, "lat": 35.4676, "lon": -97.5164,
     "tags": {"shop": "laundry", "name": "Suds City", "opening_hours": "24/7"}},
    {"type": "node", "id": 2, "lat": 35.4700, "lon": -97.5200,
     "tags": {"leisure": "fitness_centre", "name": "Iron Works Gym"}},
    {"type": "node", "id": 80, "lat": 35.4701, "lon": -97.5201,
     "tags": {"shop": "convenience", "name": "Corner Store"}},
    {"type": "node", "id": 90, "lat": 35.4700, "lon": -97.5200,
     "tags": {"amenity": "vending_machine"}},
]


def install_fake_overpass(monkeypatch, world=FAKE_WORLD):
    """Serve the fake world, honouring whatever tag filters the query asks for.

    Matching on the query text rather than returning a fixed payload matters:
    it is what lets a test assert that ``--category laundromat`` really does
    narrow the search.
    """
    def fake_run(self, query, use_cache=True, max_age_s=0):
        wanted = set(re.findall(r'\["([^"]+)"="([^"]+)"\]', query))
        elements = [
            element for element in world
            if any(pair in wanted for pair in element["tags"].items())
        ]
        return {"elements": elements}

    monkeypatch.setattr(OverpassClient, "run", fake_run)


def test_find_locations_scores_and_stores(monkeypatch, settings, db):
    install_fake_overpass(monkeypatch)
    result = find_locations(settings, db, categories=["laundromat", "gym"], progress=None)

    assert result.count == 2
    assert result.new_sites == 2
    assert result.existing_machines == 1

    names = {s.name for s in result.sites}
    assert names == {"Suds City", "Iron Works Gym"}
    assert result.sites == sorted(result.sites, key=lambda s: s.score, reverse=True)

    # The gym has a machine on site and a store next door; the laundromat has neither.
    gym = next(s for s in result.sites if s.name == "Iron Works Gym")
    laundromat = next(s for s in result.sites if s.name == "Suds City")
    assert gym.vending_nearby == 1
    assert gym.competitors_nearby == 1
    assert laundromat.competitors_nearby == 0
    assert laundromat.score > gym.score

    # And it all landed in the database.
    assert db.stats()["sites"] == 2
    assert db.recent_runs()[0]["kind"] == "locations"


def test_find_locations_rerun_adds_no_duplicates(monkeypatch, settings, db):
    install_fake_overpass(monkeypatch)
    find_locations(settings, db, categories=["laundromat"])
    second = find_locations(settings, db, categories=["laundromat"])
    assert second.new_sites == 0
    assert db.stats()["sites"] == 1


def test_find_locations_assigns_territories(monkeypatch, settings, db):
    install_fake_overpass(monkeypatch)
    result = find_locations(settings, db, categories=["laundromat", "gym"], territories=2)
    assert {s.territory for s in result.sites} == {0, 1}
    assert db.query_sites(territory=0)


def test_find_locations_rejects_unknown_category(settings, db):
    with pytest.raises(ValueError, match="unknown category"):
        find_locations(settings, db, categories=["helipad"])


def test_plan_service_route_orders_stops(monkeypatch, settings, db):
    install_fake_overpass(monkeypatch)
    sites = find_locations(settings, db, categories=["laundromat", "gym"]).sites
    route = plan_service_route(sites, start=(35.4676, -97.5164))
    assert len(route.stops) == len(sites)
    assert route.distance_mi > 0
    assert {s.id for s in route.stops} == {s.id for s in sites}


def test_plan_service_route_on_empty_input():
    route = plan_service_route([])
    assert route.stops == [] and route.distance_m == 0.0
