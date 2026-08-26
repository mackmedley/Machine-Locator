from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from machine_locator.routes.base import dedupe
from machine_locator.routes.html_source import HtmlSource, heuristic_extract
from machine_locator.routes.registry import build_sources, load_source_configs
from machine_locator.routes.rss_source import RssSource
from machine_locator.models import RouteListing

FIXTURES = Path(__file__).parent / "fixtures"

CONFIG = {
    "name": "demo",
    "label": "Demo Broker",
    "urls": ["https://broker.example/search"],
    "item_selector": ".listing-card",
    "fields": {
        "title": ".listing-title",
        "url": {"selector": "a", "attr": "href"},
        "price": ".asking-price",
        "cash_flow": ".cash-flow",
        "location": ".location",
        "description": ".description",
    },
}


def parse_fixture(name, config=CONFIG):
    soup = BeautifulSoup((FIXTURES / name).read_text(), "lxml")
    return HtmlSource(config).parse_page(soup, "https://broker.example/search")


def test_html_source_extracts_all_fields():
    listings = parse_fixture("listings_page.html")
    top = next(l for l in listings if "34 Machines" in l.title)
    assert top.price == 78_500
    assert top.cash_flow == 42_000
    assert top.gross_revenue == 186_000
    assert top.machine_count == 34
    assert top.location_text == "Oklahoma City, OK"
    assert top.is_local
    assert top.url == "https://broker.example/listing/1001"


def test_html_source_scores_out_of_state_lower():
    listings = parse_fixture("listings_page.html")
    okc = next(l for l in listings if "34 Machines" in l.title)
    dallas = next(l for l in listings if "Dallas" in l.location_text)
    assert okc.relevance > dallas.relevance


def test_non_vending_listing_scores_zero():
    listings = parse_fixture("listings_page.html")
    laundromat = next(l for l in listings if "Laundromat" in l.title)
    assert laundromat.relevance == 0.0


def test_heuristic_fallback_survives_a_redesign():
    soup = BeautifulSoup((FIXTURES / "listings_redesigned.html").read_text(), "lxml")
    listings = heuristic_extract(soup, "https://broker.example/search", "demo")
    titles = [l.title for l in listings]
    assert any("Snack Vending Route" in t for t in titles)
    # Off-site sponsored links are skipped.
    assert not any("somewhere offsite" in t for t in titles)


def test_heuristic_fallback_reads_price_from_context():
    soup = BeautifulSoup((FIXTURES / "listings_redesigned.html").read_text(), "lxml")
    listing = heuristic_extract(soup, "https://broker.example/search", "demo")[0]
    assert listing.price == 46_000
    assert listing.machine_count == 18


def test_configured_selector_that_matches_nothing_yields_nothing():
    config = dict(CONFIG, item_selector=".does-not-exist")
    assert parse_fixture("listings_page.html", config) == []


RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Vending Route For Sale - 24 machines - $52,000</title>
<link>https://example.org/ads/1</link>
<description>Snack and soda route. Cash Flow: $41,000</description>
<pubDate>Mon, 24 Aug 2026 09:00:00 GMT</pubDate></item>
<item><title>Sofa</title><link>https://example.org/ads/2</link><description>Blue</description></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Vending business for sale, 40 machines</title>
<link href="https://example.org/a/9"/><summary>Asking $150,000</summary></entry></feed>"""


def test_rss_source_parses_items():
    source = RssSource({"name": "cl", "urls": [], "default_location": "Oklahoma City, OK"})
    listings = source.parse_feed(RSS)
    assert len(listings) == 2
    route = listings[0]
    assert route.price == 52_000
    assert route.cash_flow == 41_000
    assert route.machine_count == 24
    assert route.is_local
    assert route.url == "https://example.org/ads/1"
    assert listings[1].relevance == 0.0


def test_rss_source_parses_atom_href_links():
    source = RssSource({"name": "cl", "urls": []})
    listing = source.parse_feed(ATOM)[0]
    assert listing.url == "https://example.org/a/9"
    assert listing.price == 150_000


def test_listing_ids_are_stable_and_distinct():
    first = RouteListing.make_id("s", "https://x/1", "Title")
    assert first == RouteListing.make_id("s", "https://X/1 ", " title")
    assert first != RouteListing.make_id("s", "https://x/2", "Title")


def test_dedupe_collapses_syndicated_copies():
    a = RouteListing(id="1", source="a", title="Vending Route  32 Machines", url="u1", price=50_000)
    b = RouteListing(id="2", source="b", title="vending route 32 machines", url="u2", price=50_000)
    c = RouteListing(id="3", source="c", title="Different route", url="u3", price=90_000)
    assert len(dedupe([a, b, c])) == 2


def test_packaged_sources_are_valid():
    configs = load_source_configs()
    assert configs, "sources.yaml should ship with sources"
    for config in configs:
        assert config.get("name")
        assert config.get("type") in {"rss", "html"}
        assert config.get("urls"), f"{config['name']} has no URLs"
        assert config.get("notes"), f"{config['name']} should explain itself"
    # Every source must instantiate.
    assert build_sources(include_disabled=True)


def test_unknown_source_type_is_rejected(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text("sources:\n  - name: bad\n    type: carrier-pigeon\n    urls: ['x']\n")
    with pytest.raises(ValueError, match="unknown type"):
        build_sources(path)
