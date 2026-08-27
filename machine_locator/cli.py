"""Command line interface.

    mloc locations find      -- prospect placement sites in the OKC metro
    mloc routes find         -- hunt for vending routes listed for sale
    mloc serve               -- browse both on a map in your browser
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Sequence

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .config import Settings
from .db import Database
from .export import (
    listings_to_csv, listings_to_json, sites_to_csv, sites_to_geojson, sites_to_json,
)
from .locations.categories import CATEGORY_SPECS
from .locations.pipeline import assign_territories, find_locations, plan_service_route
from .locations.scoring import machine_recommendation
from .models import RouteListing, Site

console = Console()

GRADE_STYLE = {"A+": "bold green", "A": "green", "B": "yellow", "C": "dark_orange", "D": "red"}


def money(value: Optional[float]) -> str:
    """Abbreviate large figures, but never round away a number a buyer needs.

    $3,500 must not print as "$4K", and a $2,308 per-machine price must not
    print as "$2K" -- those are the digits you negotiate on.
    """
    if value is None:
        return "-"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 10_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:,.0f}"


def get_settings(ctx: click.Context) -> Settings:
    return ctx.obj["settings"]


def open_db(ctx: click.Context) -> Database:
    settings = get_settings(ctx)
    settings.ensure_dirs()
    return Database(settings.db_path)


def progress(message: str) -> None:
    console.print(f"  [dim]{message}[/dim]")


# --------------------------------------------------------------------- root


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="machine-locator")
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Where the database and exports live (default: ~/.machine-locator).",
)
@click.option("--city", default=None, help="City name used for labelling and strict-limit searches.")
@click.pass_context
def main(ctx: click.Context, data_dir: Optional[Path], city: Optional[str]) -> None:
    """Find vending machine locations in Oklahoma City, and vending routes for sale."""
    settings = Settings()
    if data_dir:
        settings.data_dir = Path(data_dir)
    if city:
        settings.city = city
    ctx.ensure_object(dict)
    ctx.obj["settings"] = settings


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show what is currently in your database."""
    settings = get_settings(ctx)
    with open_db(ctx) as db:
        stats = db.stats()
        body = (
            f"[bold]Sites[/bold]          {stats['sites']:,} "
            f"({stats['sites_a_grade']:,} graded A or better)\n"
            f"[bold]Route listings[/bold] {stats['listings']:,} "
            f"({stats['listings_local']:,} in the OKC metro)\n"
            f"[bold]Last site scan[/bold]  {stats['last_site_run'] or 'never'}\n"
            f"[bold]Last route scan[/bold] {stats['last_route_run'] or 'never'}\n"
            f"[bold]Database[/bold]       {settings.db_path}"
        )
        console.print(Panel(body, title=f"Machine Locator -- {settings.city}", expand=False))

        runs = db.recent_runs(5)
        if runs:
            table = Table(title="Recent runs", show_header=True, header_style="bold")
            for column in ("when", "kind", "found", "new", "notes"):
                table.add_column(column)
            for run in runs:
                table.add_row(
                    str(run["finished_at"])[:19], run["kind"], str(run["found"]),
                    str(run["new_items"]), (run["notes"] or "")[:60],
                )
            console.print(table)


# ---------------------------------------------------------------- locations


@main.group()
def locations() -> None:
    """Find and rank places to put a machine."""


def _sites_table(sites: Sequence[Site], title: str) -> Table:
    table = Table(title=title, show_header=True, header_style="bold", expand=True)
    table.add_column("#", width=4, justify="right")
    table.add_column("Score", width=6, justify="right")
    table.add_column("Name", ratio=3, no_wrap=False)
    table.add_column("Type", ratio=2)
    table.add_column("Address", ratio=3, no_wrap=False)
    table.add_column("Comp", width=5, justify="right")
    table.add_column("Sell", ratio=2)
    for index, site in enumerate(sites, start=1):
        style = GRADE_STYLE.get(site.grade, "")
        table.add_row(
            str(index),
            f"[{style}]{site.score:.0f} {site.grade}[/{style}]" if style else f"{site.score:.0f}",
            site.name,
            site.category_label,
            site.address or f"{site.lat:.4f}, {site.lon:.4f}",
            str(site.competitors_nearby),
            machine_recommendation(site),
        )
    return table


@locations.command("find")
@click.option("--category", "-c", multiple=True, help="Limit to categories (repeatable). See `mloc locations categories`.")
@click.option("--territories", "-t", default=0, help="Split results into N drivable service territories.")
@click.option("--strict-city", is_flag=True, help="Clip to the city boundary instead of the metro bounding box.")
@click.option("--refresh", is_flag=True, help="Ignore the local Overpass cache and re-download.")
@click.option("--top", default=25, help="How many results to print.")
@click.option("--min-score", default=0.0, help="Hide anything below this score.")
@click.pass_context
def locations_find(
    ctx: click.Context, category: Sequence[str], territories: int,
    strict_city: bool, refresh: bool, top: int, min_score: float,
) -> None:
    """Search OpenStreetMap for candidate sites and score them."""
    settings = get_settings(ctx)
    console.print(f"[bold]Prospecting {settings.city}[/bold]")
    with open_db(ctx) as db:
        try:
            result = find_locations(
                settings, db,
                categories=list(category) or None,
                area_name=settings.city if strict_city else None,
                use_cache=not refresh,
                territories=territories,
                progress=progress,
            )
        except ValueError as exc:
            raise click.ClickException(str(exc))
        except Exception as exc:
            raise click.ClickException(
                f"location search failed: {exc}\n"
                "Overpass mirrors are free and sometimes busy -- wait a minute and retry."
            )

        shown = [s for s in result.sites if s.score >= min_score][:top]
        console.print()
        console.print(_sites_table(shown, f"Top {len(shown)} placement prospects"))
        console.print(
            f"\n[green]{result.count:,} sites scored[/green] "
            f"({result.new_sites:,} new since your last scan). "
            f"Mapped {result.competitors:,} competing food/drink outlets and "
            f"{result.existing_machines:,} existing machines.\n"
            f"Next: [bold]mloc locations list --top 50[/bold] or "
            f"[bold]mloc export sites -o prospects.csv[/bold]"
        )


@locations.command("list")
@click.option("--top", default=25)
@click.option("--category", "-c", default=None)
@click.option("--territory", default=None, type=int)
@click.option("--min-score", default=0.0)
@click.option("--grade", default=None, help="Filter by letter grade, e.g. A.")
@click.option("--search", default=None, help="Match against name or address.")
@click.pass_context
def locations_list(
    ctx: click.Context, top: int, category: Optional[str], territory: Optional[int],
    min_score: float, grade: Optional[str], search: Optional[str],
) -> None:
    """List stored sites without re-running a search."""
    with open_db(ctx) as db:
        sites = db.query_sites(
            limit=top * 4 if grade else top, min_score=min_score,
            category=category, territory=territory, search=search,
        )
        if grade:
            sites = [s for s in sites if s.grade.upper() == grade.upper()]
        sites = sites[:top]
        if not sites:
            console.print("[yellow]No sites match. Run `mloc locations find` first.[/yellow]")
            return
        console.print(_sites_table(sites, f"{len(sites)} stored prospects"))


@locations.command("show")
@click.argument("query")
@click.pass_context
def locations_show(ctx: click.Context, query: str) -> None:
    """Show the full scoring breakdown for one site."""
    with open_db(ctx) as db:
        matches = db.query_sites(limit=1, search=query)
        if not matches:
            raise click.ClickException(f"no stored site matching '{query}'")
        site = matches[0]

        style = GRADE_STYLE.get(site.grade, "")
        console.print(Panel(
            f"[{style}]{site.score:.1f} / 100  (grade {site.grade})[/{style}]\n"
            f"{site.category_label}\n"
            f"{site.address or '(no address on file)'}\n"
            f"{site.phone}  {site.website}\n"
            f"Hours: {site.opening_hours or 'unknown'}\n"
            f"Map: https://www.google.com/maps/search/?api=1&query={site.lat},{site.lon}\n"
            f"Stock it with: {machine_recommendation(site)}",
            title=site.name, expand=False,
        ))

        table = Table(title="Score components (0-10, weighted)", show_header=True)
        table.add_column("Component")
        table.add_column("Value", justify="right")
        for key, value in site.breakdown.items():
            table.add_row(key.replace("_", " "), f"{value:.2f}")
        console.print(table)

        console.print("[bold]Why:[/bold]")
        for reason in site.reasons:
            console.print(f"  - {reason}")


@locations.command("categories")
@click.pass_context
def locations_categories(ctx: click.Context) -> None:
    """List the site types this tool knows how to score."""
    with open_db(ctx) as db:
        counts = {row["category"]: row for row in db.site_categories()}

    table = Table(title="Vending placement categories", show_header=True, header_style="bold")
    table.add_column("key", no_wrap=True)
    table.add_column("type")
    table.add_column("traffic", justify="right", width=7)
    table.add_column("dwell", justify="right", width=6)
    table.add_column("captive", justify="right", width=7)
    table.add_column("hard to win", justify="right", width=11)
    table.add_column("in db", justify="right", width=6)
    for spec in sorted(CATEGORY_SPECS, key=lambda s: -(s.traffic + s.dwell + s.captivity)):
        row = counts.get(spec.key)
        table.add_row(
            spec.key, spec.label, f"{spec.traffic:.0f}", f"{spec.dwell:.0f}",
            f"{spec.captivity:.0f}", f"{spec.difficulty:.0f}",
            str(row["n"]) if row else "-",
        )
    console.print(table)
    console.print("[dim]Tune these priors in machine_locator/locations/categories.py "
                  "as your own placements report back.[/dim]")


@locations.command("route")
@click.option("--top", default=20, help="How many of the best prospects to include.")
@click.option("--territory", default=None, type=int)
@click.option("--category", "-c", default=None)
@click.option("--min-score", default=60.0)
@click.option("--start", default=None, help="Start/end point as 'lat,lon' (your warehouse).")
@click.pass_context
def locations_route(
    ctx: click.Context, top: int, territory: Optional[int],
    category: Optional[str], min_score: float, start: Optional[str],
) -> None:
    """Order the best prospects into an efficient driving run.

    Useful twice: for a day of cold-calling, and later as the service route
    once the machines are in.
    """
    start_point = None
    if start:
        try:
            lat_text, lon_text = start.split(",")
            start_point = (float(lat_text), float(lon_text))
        except ValueError:
            raise click.ClickException("--start must look like '35.4676,-97.5164'")

    with open_db(ctx) as db:
        sites = db.query_sites(
            limit=top, min_score=min_score, category=category, territory=territory
        )
    if not sites:
        raise click.ClickException("no sites match -- try lowering --min-score")

    route = plan_service_route(sites, start=start_point)
    table = Table(title=f"Run of {len(route.stops)} stops -- {route.distance_mi:.1f} miles", show_header=True, expand=True)
    table.add_column("Stop", width=5, justify="right")
    table.add_column("Score", width=6, justify="right")
    table.add_column("Name", ratio=3)
    table.add_column("Address", ratio=3)
    for index, site in enumerate(route.stops, start=1):
        table.add_row(str(index), f"{site.score:.0f}", site.name,
                      site.address or f"{site.lat:.4f}, {site.lon:.4f}")
    console.print(table)
    if start_point:
        console.print(f"[dim]Starting and returning to {start_point[0]}, {start_point[1]}[/dim]")


@locations.command("territories")
@click.option("--count", "-n", default=4, help="How many territories to create.")
@click.option("--min-score", default=50.0)
@click.pass_context
def locations_territories(ctx: click.Context, count: int, min_score: float) -> None:
    """Split stored prospects into N geographic service territories."""
    with open_db(ctx) as db:
        sites = db.query_sites(limit=100_000, min_score=min_score)
        if not sites:
            raise click.ClickException("no sites stored -- run `mloc locations find` first")
        assignments = assign_territories(sites, count)
        db.update_site_territories(assignments)

        table = Table(title=f"{count} service territories", show_header=True)
        for column in ("territory", "sites", "avg score", "centre"):
            table.add_column(column, justify="right" if column != "centre" else "left")
        for territory in range(count):
            members = [s for s in sites if s.territory == territory]
            if not members:
                continue
            avg = sum(s.score for s in members) / len(members)
            lat = sum(s.lat for s in members) / len(members)
            lon = sum(s.lon for s in members) / len(members)
            table.add_row(str(territory), str(len(members)), f"{avg:.1f}", f"{lat:.4f}, {lon:.4f}")
        console.print(table)
        console.print("[dim]Filter with `mloc locations list --territory 0`.[/dim]")


# ------------------------------------------------------------------- routes


@main.group("routes")
def routes_group() -> None:
    """Find vending routes and vending businesses listed for sale."""


def _listings_table(listings: Sequence[RouteListing], title: str) -> Table:
    table = Table(title=title, show_header=True, header_style="bold", expand=True)
    table.add_column("Fit", width=4, justify="right")
    table.add_column("Listing", ratio=4, no_wrap=False)
    table.add_column("Price", width=8, justify="right")
    table.add_column("Cash flow", width=10, justify="right")
    table.add_column("Mach", width=5, justify="right")
    table.add_column("Where", ratio=2)
    table.add_column("Source", ratio=1, overflow="fold")
    for listing in listings:
        marker = "[bold green]" if listing.is_local else ""
        close = "[/bold green]" if listing.is_local else ""
        table.add_row(
            f"{listing.relevance:.0f}",
            f"{marker}{listing.title[:90]}{close}",
            money(listing.price),
            money(listing.cash_flow),
            str(listing.machine_count) if listing.machine_count else "-",
            (listing.location_text or "-")[:28],
            listing.source,
        )
    return table


@routes_group.command("find")
@click.option("--source", "-s", multiple=True, help="Limit to specific sources (repeatable).")
@click.option("--min-relevance", default=25.0, help="Drop listings below this fit score.")
@click.option("--limit", default=100, help="Max listings to take per source.")
@click.option("--ignore-robots", is_flag=True,
              help="Fetch pages that robots.txt disallows. Your call, your liability.")
@click.option("--new-only", is_flag=True, help="Only print listings never seen before.")
@click.pass_context
def routes_find(
    ctx: click.Context, source: Sequence[str], min_relevance: float,
    limit: int, ignore_robots: bool, new_only: bool,
) -> None:
    """Poll every source for vending routes on the market."""
    from .routes.pipeline import find_routes

    settings = get_settings(ctx)
    if ignore_robots:
        console.print("[yellow]--ignore-robots: fetching pages that robots.txt disallows.[/yellow]")

    console.print("[bold]Searching for vending routes for sale[/bold]")
    with open_db(ctx) as db:
        try:
            result = find_routes(
                settings, db,
                sources=list(source) or None,
                min_relevance=min_relevance,
                limit_per_source=limit,
                respect_robots=False if ignore_robots else None,
                progress=progress,
            )
        except ValueError as exc:
            raise click.ClickException(str(exc))

        listings = result.new_listings if new_only else result.listings
        console.print()
        if listings:
            label = "new listings" if new_only else "listings"
            console.print(_listings_table(listings[:40], f"{len(listings)} {label}"))
            console.print("[dim]Green rows are in the OKC metro. "
                          "Fit is 0-100: does this look like a real route?[/dim]")
        else:
            console.print("[yellow]No matching listings this run.[/yellow]")

        blocked = result.blocked_sources
        failed = result.failed_sources
        if blocked or failed:
            console.print()
            for report in blocked:
                console.print(f"[yellow]{report.label} skipped:[/yellow] {report.skipped}")
            for report in failed:
                console.print(f"[red]{report.label} failed:[/red] {report.error}")
            console.print("[dim]Run `mloc routes diagnose` to see exactly what each "
                          "source returned.[/dim]")

        console.print(
            f"\n{result.count} listing(s) kept, {len(result.new_listings)} new. "
            f"Review with [bold]mloc routes list --local-only[/bold]"
        )


@routes_group.command("list")
@click.option("--top", default=30)
@click.option("--source", "-s", default=None)
@click.option("--local-only", is_flag=True, help="OKC metro listings only.")
@click.option("--min-relevance", default=0.0)
@click.option("--max-price", default=None, type=float, help="Hide anything above this asking price.")
@click.option("--new-since-days", default=None, type=int, help="Only listings first seen in the last N days.")
@click.pass_context
def routes_list(
    ctx: click.Context, top: int, source: Optional[str], local_only: bool,
    min_relevance: float, max_price: Optional[float], new_since_days: Optional[int],
) -> None:
    """List stored route listings."""
    since = None
    if new_since_days is not None:
        since = (datetime.now(timezone.utc) - timedelta(days=new_since_days)).isoformat(timespec="seconds")

    with open_db(ctx) as db:
        listings = db.query_listings(
            limit=top, min_relevance=min_relevance, source=source,
            local_only=local_only, since=since, max_price=max_price,
        )
    if not listings:
        console.print("[yellow]Nothing stored yet. Run `mloc routes find`.[/yellow]")
        return
    console.print(_listings_table(listings, f"{len(listings)} route listings"))


@routes_group.command("show")
@click.argument("index", type=int)
@click.option("--local-only", is_flag=True)
@click.pass_context
def routes_show(ctx: click.Context, index: int, local_only: bool) -> None:
    """Show one listing in full, by its row number from `routes list`."""
    with open_db(ctx) as db:
        listings = db.query_listings(limit=max(index, 1), local_only=local_only)
    if index < 1 or index > len(listings):
        raise click.ClickException(f"no listing #{index}")
    listing = listings[index - 1]

    per_machine = ""
    if listing.price and listing.machine_count:
        per_machine = f"\nPer machine: {money(listing.price / listing.machine_count)}"
    multiple = ""
    if listing.price and listing.cash_flow:
        multiple = f"\nMultiple of cash flow: {listing.price / listing.cash_flow:.1f}x"

    console.print(Panel(
        f"[bold]{listing.title}[/bold]\n\n"
        f"Asking: {money(listing.price)}   Cash flow: {money(listing.cash_flow)}   "
        f"Gross: {money(listing.gross_revenue)}\n"
        f"Machines: {listing.machine_count or 'not stated'}"
        f"{per_machine}{multiple}\n"
        f"Location: {listing.location_text or 'not stated'}\n"
        f"Source: {listing.source}   Posted: {listing.posted_at or 'unknown'}   "
        f"First seen: {listing.first_seen[:10]}\n"
        f"{listing.url}\n\n"
        f"{listing.description[:900] or '(no description captured)'}\n\n"
        f"[dim]Fit {listing.relevance:.0f}/100: {'; '.join(listing.relevance_reasons)}[/dim]",
        title=f"Listing #{index}", expand=True,
    ))


@routes_group.command("sources")
@click.pass_context
def routes_sources(ctx: click.Context) -> None:
    """List the configured listing sources."""
    from .routes.registry import load_source_configs, DEFAULT_SOURCES_FILE

    table = Table(title="Route listing sources", show_header=True, header_style="bold")
    table.add_column("name", no_wrap=True)
    table.add_column("type", width=6)
    table.add_column("on", width=4)
    table.add_column("what it is", overflow="fold")
    for config in load_source_configs():
        table.add_row(
            str(config.get("name")), str(config.get("type", "html")),
            "yes" if config.get("enabled", True) else "no",
            " ".join(str(config.get("notes", "")).split())[:220],
        )
    console.print(table)
    console.print(f"[dim]Edit {DEFAULT_SOURCES_FILE} to add a source or repair a selector.[/dim]")


@routes_group.command("diagnose")
@click.option("--source", "-s", multiple=True)
@click.option("--ignore-robots", is_flag=True)
@click.option("--save-html", type=click.Path(path_type=Path), default=None,
              help="Write each fetched page here so you can inspect the markup.")
@click.pass_context
def routes_diagnose(
    ctx: click.Context, source: Sequence[str], ignore_robots: bool, save_html: Optional[Path]
) -> None:
    """Check every source URL and report why it does or does not return results."""
    from .routes.pipeline import diagnose

    settings = get_settings(ctx)
    results = diagnose(
        settings, sources=list(source) or None,
        respect_robots=False if ignore_robots else None,
        save_html_to=save_html,
    )
    table = Table(title="Source diagnosis", show_header=True, header_style="bold", expand=True)
    table.add_column("source", no_wrap=True)
    table.add_column("status", width=10)
    table.add_column("robots", width=22)
    table.add_column("sel", width=4, justify="right")
    table.add_column("fallback", width=8, justify="right")
    table.add_column("notes", ratio=3, no_wrap=False)
    for entry in results:
        note = entry.hint or (f"e.g. {entry.sample_title}" if entry.sample_title else "")
        table.add_row(
            entry.name, entry.status, entry.robots, str(entry.configured_matches),
            str(entry.fallback_matches), note,
        )
    console.print(table)
    console.print("[dim]'sel' is how many cards your item_selector matched; 'fallback' is how "
                  "many the heuristic extractor found. Fix selectors in "
                  "machine_locator/routes/sources.yaml.[/dim]")


@routes_group.command("import")
@click.argument("csv_path", type=click.Path(exists=True, path_type=Path))
@click.option("--source-name", default="imported", help="Label these rows in the database.")
@click.pass_context
def routes_import(ctx: click.Context, csv_path: Path, source_name: str) -> None:
    """Import listings from a CSV -- e.g. a broker's saved-search export.

    Column names are matched loosely, so an unedited marketplace export
    usually just works.
    """
    from .routes.pipeline import import_csv

    with open_db(ctx) as db:
        listings = import_csv(db, csv_path, source_name=source_name)
    if not listings:
        console.print("[yellow]No rows with a usable title column.[/yellow]")
        return
    console.print(_listings_table(listings[:40], f"Imported {len(listings)} listings"))


# ------------------------------------------------------------------- export


@main.command("export")
@click.argument("what", type=click.Choice(["sites", "listings"]))
@click.option("--format", "-f", "fmt", type=click.Choice(["csv", "geojson", "json"]), default="csv")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None)
@click.option("--top", default=1000)
@click.option("--min-score", default=0.0, help="For sites: minimum score.")
@click.option("--local-only", is_flag=True, help="For listings: OKC metro only.")
@click.pass_context
def export_cmd(
    ctx: click.Context, what: str, fmt: str, output: Optional[Path],
    top: int, min_score: float, local_only: bool,
) -> None:
    """Export to CSV, GeoJSON (for Google My Maps) or JSON."""
    settings = get_settings(ctx)
    settings.ensure_dirs()
    path = Path(output) if output else settings.export_dir / f"{what}.{fmt}"

    with open_db(ctx) as db:
        if what == "sites":
            sites = db.query_sites(limit=top, min_score=min_score)
            if not sites:
                raise click.ClickException("no sites stored -- run `mloc locations find` first")
            writer = {"csv": sites_to_csv, "geojson": sites_to_geojson, "json": sites_to_json}[fmt]
            writer(sites, path)
            count = len(sites)
        else:
            if fmt == "geojson":
                raise click.ClickException("listings have no coordinates -- use csv or json")
            listings = db.query_listings(limit=top, local_only=local_only)
            if not listings:
                raise click.ClickException("no listings stored -- run `mloc routes find` first")
            writer = {"csv": listings_to_csv, "json": listings_to_json}[fmt]
            writer(listings, path)
            count = len(listings)

    console.print(f"[green]Wrote {count:,} {what} to {path}[/green]")
    if fmt == "geojson":
        console.print("[dim]Import it at google.com/mymaps or drag it onto geojson.io.[/dim]")


def _serve(
    ctx: click.Context, host: str, port: int, debug: bool,
    open_browser: bool, friendly: bool = False,
) -> None:
    from .web.app import create_app
    from .web.auth import is_loopback

    settings = get_settings(ctx)
    settings.ensure_dirs()
    # Reachable from outside this machine? Then a password is mandatory, and
    # the app walks you through setting one on first open.
    public = not is_loopback(host)
    app = create_app(settings, public=public)
    url = f"http://{host}:{port}"

    if friendly:
        # Somebody who double-clicked an icon should not be told their app is
        # an unsuitable "development server", nor watch HTTP request logs
        # scroll past. Developers running `mloc serve` still get both.
        import logging

        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        console.print(Panel(
            "[bold green]Machine Locator is open in your browser.[/bold green]\n\n"
            f"If it didn't open, go to [bold]{url}[/bold]\n\n"
            "[dim]Leave this window open while you work.\n"
            "Closing it, or pressing Ctrl+C, shuts the app down.[/dim]",
            title="Ready", expand=False,
        ))
    else:
        lock = ("\n\n[yellow]Reachable from other machines -- it will ask you to\n"
                "pick a password the first time you open it.[/yellow]"
                if public else
                "\n\n[dim]Running privately on this machine. No password needed;\n"
                "you can still set one in Settings.[/dim]")
        console.print(Panel(
            f"[bold green]Machine Locator is running[/bold green]\n\n"
            f"Open [bold]{url}[/bold] in your browser.\n"
            f"Press Ctrl+C here to stop it.{lock}",
            expand=False,
        ))

    if open_browser:
        # Give the server a moment to bind before the browser asks for the page.
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        # The reloader spawns a second process, which would open the browser
        # twice and start duplicate background jobs.
        if friendly:
            # Serve through Werkzeug directly: Flask's own run() echoes
            # "Serving Flask app..." and "Debug mode: off", which mean nothing
            # to somebody who double-clicked an icon.
            from werkzeug.serving import run_simple

            run_simple(host, port, app, use_reloader=False, use_debugger=False,
                       threaded=True)
        else:
            app.run(host=host, port=port, debug=debug, use_reloader=False)
    except KeyboardInterrupt:
        console.print("\n[dim]Machine Locator stopped. Your data is saved.[/dim]")
    except OSError as exc:
        raise click.ClickException(
            f"Could not start on port {port}: {exc}\n"
            f"Something else is probably using it -- try `mloc app --port 5050`."
        )


@main.command("app")
@click.option("--port", default=5000, help="Port to run on.")
@click.option("--host", default="127.0.0.1")
@click.option("--no-open", is_flag=True, help="Don't open a browser window.")
@click.pass_context
def app_cmd(ctx: click.Context, port: int, host: str, no_open: bool) -> None:
    """Start Machine Locator and open it in your browser.

    This is the one command you need -- everything the other commands do has a
    button in the web app.
    """
    _serve(ctx, host, port, debug=False, open_browser=not no_open, friendly=True)


@main.command("serve")
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=5000)
@click.option("--debug", is_flag=True)
@click.option("--open/--no-open", "open_browser", default=False,
              help="Open a browser window once the server is up.")
@click.pass_context
def serve_cmd(ctx: click.Context, host: str, port: int, debug: bool, open_browser: bool) -> None:
    """Run the web app without opening a browser (for remote or scripted use)."""
    _serve(ctx, host, port, debug, open_browser)


if __name__ == "__main__":  # pragma: no cover
    main()
