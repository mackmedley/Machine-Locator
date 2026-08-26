import csv

import pytest
from click.testing import CliRunner

from machine_locator.cli import main, money

CSV_TEXT = """Business Name,Asking Price,Cash Flow,Location,Description
"Vending Route - 40 machines","$95,000","$44,000","Oklahoma City, OK","Snack and drink route"
"""


@pytest.fixture
def run(tmp_path):
    # rich reads COLUMNS; pin it so table output is deterministic across
    # whatever terminal the suite happens to run in.
    runner = CliRunner(env={"COLUMNS": "160", "TERM": "dumb"})

    def invoke(*args):
        return runner.invoke(main, ["--data-dir", str(tmp_path)] + list(args))

    return invoke


def test_money_formatting():
    assert money(None) == "-"
    assert money(950) == "$950"
    assert money(95_000) == "$95K"
    assert money(1_250_000) == "$1.25M"


def test_money_keeps_exact_dollars_below_ten_thousand():
    # $3,500 must never print as "$4K" -- these are negotiating figures.
    assert money(3_500) == "$3,500"
    assert money(2_308) == "$2,308"
    assert money(9_999) == "$9,999"
    assert money(10_000) == "$10K"


def test_status_on_an_empty_database(run):
    result = run("status")
    assert result.exit_code == 0
    assert "Machine Locator" in result.output
    assert "never" in result.output


def test_categories_lists_the_priors(run):
    result = run("locations", "categories")
    assert result.exit_code == 0
    assert "laundromat" in result.output
    assert "Manufacturing" in result.output


def test_routes_sources_lists_packaged_sources(run):
    result = run("routes", "sources")
    assert result.exit_code == 0
    assert "craigslist_okc" in result.output
    assert "bizbuysell" in result.output


def test_import_then_list_then_export(run, tmp_path):
    csv_path = tmp_path / "in.csv"
    csv_path.write_text(CSV_TEXT)

    imported = run("routes", "import", str(csv_path), "--source-name", "manual")
    assert imported.exit_code == 0
    assert "Imported 1 listings" in imported.output

    listed = run("routes", "list", "--local-only")
    assert listed.exit_code == 0
    assert "Vending Route" in listed.output

    out = tmp_path / "out.csv"
    exported = run("export", "listings", "-o", str(out))
    assert exported.exit_code == 0
    rows = list(csv.DictReader(out.open()))
    assert rows[0]["machine_count"] == "40"
    assert rows[0]["is_local"] == "yes"


def test_listing_commands_are_graceful_when_empty(run):
    assert "Nothing stored yet" in run("routes", "list").output
    assert "No sites match" in run("locations", "list").output


def test_export_without_data_fails_with_advice(run, tmp_path):
    result = run("export", "sites", "-o", str(tmp_path / "x.csv"))
    assert result.exit_code != 0
    assert "locations find" in result.output


def test_export_geojson_is_rejected_for_listings(run, tmp_path):
    csv_path = tmp_path / "in.csv"
    csv_path.write_text(CSV_TEXT)
    run("routes", "import", str(csv_path))
    result = run("export", "listings", "-f", "geojson", "-o", str(tmp_path / "x.geojson"))
    assert result.exit_code != 0
    assert "no coordinates" in result.output


def test_route_command_validates_start_coordinates(run):
    result = run("locations", "route", "--start", "not-a-coordinate")
    assert result.exit_code != 0
    assert "35.4676" in result.output


def test_help_mentions_both_halves_of_the_tool(run):
    output = run("--help").output
    assert "locations" in output
    assert "routes" in output
