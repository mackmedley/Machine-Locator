import csv
import json

from machine_locator.export import (
    listings_to_csv, listings_to_json, sites_to_csv, sites_to_geojson,
)
from machine_locator.models import RouteListing, Site


def make_site(site_id="node/1", score=80.0):
    return Site(
        id=site_id, name="Suds Laundromat", category="laundromat",
        category_label="Laundromat", lat=35.4676, lon=-97.5164,
        address="123 NW 23rd St", score=score, grade="A",
        breakdown={"traffic": 6.0}, reasons=["captive audience"],
    )


def make_listing(listing_id="abc", relevance=90.0, local=True):
    return RouteListing(
        id=listing_id, source="demo", title="Vending Route -- 30 machines",
        url="https://example.org/1", price=75_000.0, cash_flow=40_000.0,
        machine_count=30, location_text="Oklahoma City, OK", relevance=relevance,
        relevance_reasons=["matches 'vending route'"], is_local=local,
    )


def test_site_roundtrip_preserves_json_fields(db):
    db.upsert_sites([make_site()])
    stored = db.query_sites(limit=10)[0]
    assert stored.reasons == ["captive audience"]
    assert stored.breakdown == {"traffic": 6.0}
    assert stored.name == "Suds Laundromat"


def test_upsert_reports_new_sites_only_once(db):
    assert db.upsert_sites([make_site()]) == 1
    assert db.upsert_sites([make_site()]) == 0
    assert db.stats()["sites"] == 1


def test_first_seen_survives_a_rescan(db):
    db.upsert_sites([make_site()])
    original = db.query_sites(limit=1)[0].first_seen

    refreshed = make_site()
    refreshed.first_seen = "2099-01-01T00:00:00+00:00"
    db.upsert_sites([refreshed])

    stored = db.query_sites(limit=1)[0]
    assert stored.first_seen == original
    assert stored.last_seen >= original


def test_site_filters(db):
    db.upsert_sites([make_site("node/1", 90.0), make_site("node/2", 40.0)])
    assert len(db.query_sites(min_score=50)) == 1
    assert len(db.query_sites(category="laundromat")) == 2
    assert len(db.query_sites(category="gym")) == 0
    assert len(db.query_sites(search="NW 23rd")) == 2


def test_territories_persist(db):
    db.upsert_sites([make_site("node/1"), make_site("node/2")])
    db.update_site_territories({"node/1": 0, "node/2": 1})
    assert db.query_sites(territory=1)[0].id == "node/2"


def test_listing_upsert_returns_only_fresh_rows(db):
    assert len(db.upsert_listings([make_listing()])) == 1
    assert len(db.upsert_listings([make_listing()])) == 0


def test_listing_filters(db):
    db.upsert_listings([
        make_listing("a", 90.0, True),
        make_listing("b", 20.0, False),
    ])
    assert len(db.query_listings(local_only=True)) == 1
    assert len(db.query_listings(min_relevance=50)) == 1
    assert len(db.query_listings(max_price=10_000)) == 0
    assert len(db.query_listings(source="demo")) == 2


def test_listing_booleans_survive_the_roundtrip(db):
    db.upsert_listings([make_listing("a", 90.0, True)])
    stored = db.query_listings()[0]
    assert stored.is_local is True
    assert stored.relevance_reasons == ["matches 'vending route'"]


def test_site_notes(db):
    db.upsert_sites([make_site()])
    db.set_site_note("node/1", "pitched", "spoke to owner Tuesday")
    notes = db.get_site_notes()
    assert notes["node/1"]["status"] == "pitched"


def test_stats_and_runs(db):
    db.upsert_sites([make_site("node/1", 90.0), make_site("node/2", 10.0)])
    db.record_run("locations", "2026-01-01T00:00:00+00:00", found=2, new_items=2)
    stats = db.stats()
    assert stats["sites"] == 2
    assert stats["sites_a_grade"] == 1
    assert stats["last_site_run"]
    assert db.recent_runs()[0]["kind"] == "locations"


def test_sites_csv_has_actionable_columns(tmp_path):
    path = sites_to_csv([make_site()], tmp_path / "sites.csv")
    rows = list(csv.DictReader(path.open()))
    assert rows[0]["name"] == "Suds Laundromat"
    assert rows[0]["grade"] == "A"
    assert "google.com/maps" in rows[0]["map_url"]
    assert rows[0]["sell_here"]


def test_geojson_is_valid_and_styled(tmp_path):
    path = sites_to_geojson([make_site()], tmp_path / "sites.geojson")
    data = json.loads(path.read_text())
    assert data["type"] == "FeatureCollection"
    feature = data["features"][0]
    assert feature["geometry"]["coordinates"] == [-97.5164, 35.4676]
    assert feature["properties"]["marker-color"]


def test_listings_csv_and_json(tmp_path):
    csv_path = listings_to_csv([make_listing()], tmp_path / "l.csv")
    rows = list(csv.DictReader(csv_path.open()))
    assert rows[0]["price"] == "75000.0"
    assert rows[0]["is_local"] == "yes"

    json_path = listings_to_json([make_listing()], tmp_path / "l.json")
    assert json.loads(json_path.read_text())[0]["machine_count"] == 30
