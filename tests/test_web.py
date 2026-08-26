import pytest

from machine_locator.db import Database
from machine_locator.models import RouteListing, Site
from machine_locator.web.app import create_app


@pytest.fixture
def app(settings):
    with Database(settings.db_path) as database:
        database.upsert_sites([
            Site(id="node/1", name="Suds Laundromat", category="laundromat",
                 category_label="Laundromat", lat=35.4676, lon=-97.5164,
                 address="123 NW 23rd St", score=88.0, grade="A+",
                 breakdown={"traffic": 6.0}, reasons=["captive audience"]),
            Site(id="node/2", name="Iron Works Gym", category="gym",
                 category_label="Gym / fitness center", lat=35.47, lon=-97.52,
                 score=41.0, grade="D"),
        ])
        database.upsert_listings([
            RouteListing(id="a", source="demo", title="Vending Route -- 30 machines",
                         url="https://example.org/1", price=75_000.0, relevance=90.0,
                         location_text="Oklahoma City, OK", is_local=True),
            RouteListing(id="b", source="demo", title="Vending business in Texas",
                         url="https://example.org/2", price=500_000.0, relevance=40.0,
                         location_text="Dallas, TX", is_local=False),
        ])
    application = create_app(settings)
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def test_map_page_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Machine Locator" in body
    assert "Oklahoma City" in body
    assert "Laundromat" in body  # category filter is populated


def test_listings_page_renders(client):
    response = client.get("/listings")
    assert response.status_code == 200
    assert "Routes for sale" in response.get_data(as_text=True)


def test_sites_api_returns_scored_sites(client):
    data = client.get("/api/sites").get_json()
    assert data["count"] == 2
    top = data["sites"][0]
    assert top["name"] == "Suds Laundromat"
    assert top["grade"] == "A+"
    assert top["sell_here"]
    assert top["reasons"] == ["captive audience"]


def test_sites_api_filters(client):
    assert client.get("/api/sites?min_score=50").get_json()["count"] == 1
    assert client.get("/api/sites?category=gym").get_json()["count"] == 1
    assert client.get("/api/sites?search=Iron").get_json()["count"] == 1
    assert client.get("/api/sites?search=nothing").get_json()["count"] == 0


def test_listings_api_filters(client):
    assert client.get("/api/listings").get_json()["count"] == 2
    assert client.get("/api/listings?local_only=1").get_json()["count"] == 1
    assert client.get("/api/listings?min_relevance=80").get_json()["count"] == 1
    assert client.get("/api/listings?max_price=100000").get_json()["count"] == 1


def test_notes_roundtrip(client):
    response = client.post("/api/sites/node/1/note",
                           json={"status": "pitched", "note": "owner said call back"})
    assert response.get_json() == {"ok": True}
    site = client.get("/api/sites?search=Suds").get_json()["sites"][0]
    assert site["note"]["status"] == "pitched"
