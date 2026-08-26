from pathlib import Path

import pytest

from machine_locator.routes.http import FetchResult, PoliteClient, RobotsDisallowed
from machine_locator.routes.pipeline import diagnose, find_routes, import_csv

FIXTURES = Path(__file__).parent / "fixtures"

SOURCES_YAML = """
sources:
  - name: demo_html
    type: html
    label: Demo Broker
    enabled: true
    urls: ["https://broker.example/search"]
    item_selector: ".listing-card"
    fields:
      title: ".listing-title"
      url: { selector: "a", attr: href }
      price: ".asking-price"
      cash_flow: ".cash-flow"
      location: ".location"
      description: ".description"
    notes: A demo source.
  - name: demo_rss
    type: rss
    label: Demo Feed
    enabled: true
    default_location: "Oklahoma City, OK"
    urls: ["https://feed.example/rss"]
    notes: A demo feed.
  - name: demo_blocked
    type: html
    label: Demo Blocked
    enabled: true
    urls: ["https://blocked.example/search"]
    notes: Always refuses.
"""

RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Vending Route For Sale - 24 machines - $52,000</title>
<link>https://feed.example/ads/1</link>
<description>Snack and soda route. Cash Flow: $41,000</description></item>
<item><title>Office chair</title><link>https://feed.example/ads/2</link>
<description>Ergonomic</description></item>
</channel></rss>"""


@pytest.fixture
def sources_file(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text(SOURCES_YAML)
    return path


@pytest.fixture
def fake_web(monkeypatch):
    """Serve fixtures instead of the internet, and refuse one host."""
    def fake_get(self, url, retries=2):
        if "blocked.example" in url:
            raise RobotsDisallowed("blocked.example returned 403 -- blocks automated access")
        if "feed.example" in url:
            return FetchResult(url=url, status=200, text=RSS)
        return FetchResult(
            url=url, status=200, text=(FIXTURES / "listings_page.html").read_text()
        )

    monkeypatch.setattr(PoliteClient, "get", fake_get)
    monkeypatch.setattr(PoliteClient, "allowed", lambda self, url: "blocked" not in url)


def test_find_routes_collects_across_sources(fake_web, settings, db, sources_file):
    result = find_routes(settings, db, config_path=sources_file, min_relevance=25.0)

    titles = {l.title for l in result.listings}
    assert any("34 Machines" in t for t in titles)
    assert any("24 machines" in t for t in titles)
    # The laundromat and the office chair are filtered out as irrelevant.
    assert not any("Laundromat" in t for t in titles)
    assert not any("Office chair" in t for t in titles)


def test_find_routes_sorts_local_listings_first(fake_web, settings, db, sources_file):
    result = find_routes(settings, db, config_path=sources_file, min_relevance=25.0)
    locality_flags = [l.is_local for l in result.listings]
    assert locality_flags == sorted(locality_flags, reverse=True)


def test_find_routes_reports_blocked_sources(fake_web, settings, db, sources_file):
    result = find_routes(settings, db, config_path=sources_file)
    blocked = result.blocked_sources
    assert [r.name for r in blocked] == ["demo_blocked"]
    assert "blocks automated access" in blocked[0].skipped
    # One source failing must not stop the others.
    assert result.count > 0


def test_find_routes_marks_new_listings_only_once(fake_web, settings, db, sources_file):
    first = find_routes(settings, db, config_path=sources_file)
    second = find_routes(settings, db, config_path=sources_file)
    assert len(first.new_listings) == first.count
    assert second.new_listings == []
    assert db.stats()["listings"] == first.count


def test_min_relevance_gate(fake_web, settings, db, sources_file):
    strict = find_routes(settings, db, config_path=sources_file, min_relevance=95.0)
    loose = find_routes(settings, db, config_path=sources_file, min_relevance=10.0)
    assert strict.count < loose.count


def test_find_routes_can_target_one_source(fake_web, settings, db, sources_file):
    result = find_routes(settings, db, config_path=sources_file, sources=["demo_rss"])
    assert {l.source for l in result.listings} == {"demo_rss"}


def test_find_routes_rejects_unknown_source(fake_web, settings, db, sources_file):
    with pytest.raises(ValueError, match="no sources selected"):
        find_routes(settings, db, config_path=sources_file, sources=["nope"])


def test_diagnose_explains_each_source(fake_web, settings, sources_file):
    results = {d.name: d for d in diagnose(settings, config_path=sources_file)}

    assert results["demo_html"].status == "HTTP 200"
    assert results["demo_html"].configured_matches == 3
    assert results["demo_rss"].configured_matches == 2
    assert results["demo_blocked"].status == "blocked"
    assert "blocks automated access" in results["demo_blocked"].hint


def test_diagnose_flags_a_stale_selector(fake_web, settings, tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text(SOURCES_YAML.replace('".listing-card"', '".gone"'))
    entry = next(d for d in diagnose(settings, config_path=path) if d.name == "demo_html")
    assert entry.configured_matches == 0
    assert entry.fallback_matches > 0
    assert "matched nothing" in entry.hint


def test_diagnose_can_save_html(fake_web, settings, sources_file, tmp_path):
    out = tmp_path / "html"
    diagnose(settings, config_path=sources_file, sources=["demo_html"], save_html_to=out)
    assert (out / "demo_html.html").exists()


# --------------------------------------------------------------- csv import

CSV_TEXT = """Business Name,Asking Price,Cash Flow,Location,Description,Listing URL
"Vending Route - 40 machines","$95,000","$44,000","Oklahoma City, OK","Snack and drink route","https://x.example/1"
"Dry cleaner","$300,000","$80,000","Norman, OK","Two locations","https://x.example/2"
"""


def test_import_csv_matches_columns_loosely(db, tmp_path):
    path = tmp_path / "export.csv"
    path.write_text(CSV_TEXT)
    listings = import_csv(db, path, source_name="bizbuysell_export")

    route = next(l for l in listings if "Vending Route" in l.title)
    assert route.price == 95_000
    assert route.cash_flow == 44_000
    assert route.machine_count == 40
    assert route.is_local
    assert route.source == "bizbuysell_export"
    assert route.relevance > 50

    # Irrelevant rows are still imported but score zero, so they sort away.
    cleaner = next(l for l in listings if "Dry cleaner" in l.title)
    assert cleaner.relevance == 0.0
    assert db.stats()["listings"] == 2


def test_import_csv_rejects_a_headerless_file(db, tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("")
    with pytest.raises(ValueError, match="no header row"):
        import_csv(db, path)


def test_import_csv_skips_rows_without_a_title(db, tmp_path):
    path = tmp_path / "partial.csv"
    path.write_text("Business Name,Asking Price\n,\"$5,000\"\n")
    assert import_csv(db, path) == []
