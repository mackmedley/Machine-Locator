# Machine Locator

Two jobs, one tool, for a vending operator working the Oklahoma City metro:

1. **Find places to put machines.** Pull every plausible host site in the metro
   from OpenStreetMap, score each one on how likely it is to pay for itself, and
   hand you a ranked call list with addresses and a map.
2. **Find routes for sale.** Watch the business-for-sale marketplaces for
   vending routes coming on the market, pull the numbers out of the ad copy, and
   flag the ones that are actually local and actually a route.

```
mloc locations find --territories 4     # rank placement prospects
mloc routes find                        # hunt for routes on the market
mloc serve                              # browse both in your browser
```

---

## Install

```bash
git clone https://github.com/mackmedley/machine-locator.git
cd machine-locator
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Python 3.9+. No API keys required -- the location data comes from
OpenStreetMap's free Overpass API.

---

## Part 1: finding placement locations

```bash
mloc locations find
```

This queries OpenStreetMap for 26 categories of site across the OKC metro
(city limits plus Edmond, Moore, Norman, Midwest City, Del City, Yukon,
Bethany and Mustang -- a delivery van does not care about municipal lines),
then scores each one.

```
  #  Score  Name                       Type                    Address                          Comp  Sell
  1  80 A   Heartland Manufacturing    Manufacturing / plant   509 W Reno Ave, OKC 73106            0  snack, cold drink, coffee
  2  80 A   Bubbles Laundromat #7      Laundromat              953 N Penn Ave, Yukon 73099          0  snack, cold drink
  3  79 A   Cattlemen's Lodge          Hotel / motel           5474 S Air Depot Blvd 73099          1  snack, cold drink, sundries
```

### How the score works

The score answers one question: *if I walked in tomorrow, how likely is this to
be a machine that pays for itself?* Seven components, each 0-10, weighted to a
0-100 result:

| Component | Weight | What it measures |
|---|---|---|
| `traffic` | 24% | How many people pass through daily, adjusted by size hints in the map data (hotel rooms, apartment units, building storeys) |
| `dwell` | 22% | How long a person is stuck there with nothing to do |
| `captivity` | 16% | How far they are from a cheaper option -- **measured**, by counting every store, cafe, drive-thru and gas station within 400m |
| `winnability` | 10% | The inverse of how locked-up the category is by national contracts |
| `hours` | 10% | Whether the machine sells around the clock or only 9-to-5 |
| `access` | 10% | How easy the site is to service -- parking, ground floor, hours a driver can show up |
| `route_density` | 8% | How many other prospects sit within 1.5km -- windshield time is most of the cost of a route |

The weights sum to exactly 1.0, which the test suite asserts.

Then a **saturation penalty** of up to 18 points if OpenStreetMap already shows
a vending machine on the site.

That `winnability` term is the one that makes this useful rather than obvious.
A hospital scores 10 for traffic and 10 for dwell -- and a one-truck operator
will essentially never win it, because a national contract already has it. The
scorer knows that, so laundromats and machine shops rise above hospitals and
schools in the ranking. `mloc locations categories` shows every prior.

Every score comes with its reasoning:

```bash
mloc locations show "Cattlemen"
```
```
79.0 / 100  (grade A)   Hotel / motel
Why:
  - 24/7 guests who will not drive out for a soda at 11pm.
  - 210 rooms -- large property
  - 1 competing option(s) within 400m
  - open 24/7 -- sells on every shift
```

### Territories and driving routes

```bash
mloc locations territories -n 4          # split the metro into 4 service areas
mloc locations list --territory 2        # work one area at a time
mloc locations route --top 20 --start 35.4676,-97.5164
```

`route` orders stops into an efficient loop (nearest-neighbour seeded, then
2-opt) from your warehouse and back. Use it for a day of cold-calling now, and
as the restocking run once the machines are in.

### Getting the list out of the terminal

```bash
mloc export sites -f csv     -o prospects.csv        # spreadsheet or CRM
mloc export sites -f geojson -o prospects.geojson    # pins for Google My Maps
```

The GeoJSON is colour-coded by grade, so it renders correctly on
[google.com/mymaps](https://www.google.com/mymaps) or geojson.io with no
styling work. The CSV includes a clickable Google Maps link per row and a
plain-English `why` column you can read on a doorstep.

---

## Part 2: finding routes for sale

```bash
mloc routes find
mloc routes list --local-only
mloc routes show 1
```

Sources are defined in [`machine_locator/routes/sources.yaml`](machine_locator/routes/sources.yaml)
-- Craigslist's business-for-sale feed, BizBuySell, BizQuest, BusinessBroker.net,
BusinessesForSale, UsedVending and the Vending Connection classifieds.

Search results for "vending" are mostly noise, so every listing is scored 0-100
for **fit**:

- **Up** for route language (`vending route`, `snack route`, `micro market`), for
  being in the OKC metro, and for disclosing a machine count or real financials.
- **Down** hard for parts lots, single machines and "wanted to buy" ads, which
  are not routes.
- **Down** for biz-op sales language (`no experience necessary`,
  `locations guaranteed`) -- not disqualifying, but flagged so you look twice.

Asking price, cash flow, gross revenue and machine count are parsed out of the
ad copy, so `mloc routes show` can give you price per machine and a multiple of
cash flow without opening the listing.

Listings are stored with a stable id and a `first_seen` date that survives
re-scans, so this works:

```bash
mloc routes list --new-since-days 7      # what came on the market this week
```

### The honest part about scraping

Business-for-sale sites do not want to be scraped, and several of them say so.
This tool takes that seriously:

- **robots.txt is honoured by default.** Craigslist disallows `/search/`, so that
  source is skipped unless you explicitly pass `--ignore-robots`. That flag
  exists, it is your call, and the tool tells you what you are overriding.
- **Requests are rate limited per domain** and the User-Agent identifies the tool
  honestly.
- **A 403 is reported, not retried.** BizBuySell fronts an anti-bot layer; when
  it refuses, you get a clear message and a suggested workaround rather than a
  silent empty result.

When a source returns nothing, find out why:

```bash
mloc routes diagnose
```
```
source        status    robots                  sel  fallback  notes
usedvending   HTTP 200  allowed                   0        12  item_selector '.card' matched nothing;
                                                                the fallback extractor found 12
bizbuysell    blocked   allowed                   0         0  bizbuysell.com returned 403 -- blocks
                                                                automated access
craigslist    blocked   disallowed by robots.txt  0         0  robots.txt on craigslist.org disallows …
```

It separates the three things that look identical from the outside: *the site
blocked us*, *the page loaded but our CSS selector is stale*, and *the page
genuinely has no vending listings today*. It also distinguishes "robots.txt
says no" from "we could not reach robots.txt at all" -- those need completely
different fixes.

### When a site blocks you anyway

Every marketplace offers a free saved search with email alerts. Set one up,
export the results, and import them:

```bash
mloc routes import ~/Downloads/bizbuysell_alert.csv --source-name bizbuysell
```

Column names are matched loosely -- `Asking Price`, `price` and `List Price` all
work -- so an unedited marketplace export usually just imports. Imported rows go
through exactly the same relevance scoring and financial parsing as scraped ones.

---

## The browser view

```bash
mloc serve
```

A map of every prospect colour-coded by grade, click-through to the full scoring
breakdown, and a sortable table of route listings with price per machine. It
reads the same database the CLI writes, so anything you have scanned is already
there.

---

## Keeping sources working

Broker sites redesign, and CSS selectors rot. The design accounts for that:

- Sources are **configured in YAML, not coded** -- repairing one is a one-line
  edit, and `mloc routes diagnose --save-html ./debug` dumps the page so you can
  find the new selector.
- When a configured selector matches nothing, a **heuristic extractor** takes
  over: it reads every vending-looking link on the page and borrows surrounding
  text for price and location. Noisier, but a redesign degrades results instead
  of zeroing them. The relevance filter cleans up the rest.

Leaving `item_selector` empty is a valid choice -- the fallback handles it. Add a
selector when you want more precision from a source you use heavily.

---

## Tuning it to your own results

The placement priors in
[`machine_locator/locations/categories.py`](machine_locator/locations/categories.py)
are industry starting points, not gospel. Each category carries `traffic`,
`dwell`, `captivity`, `access` and `difficulty` values with a comment explaining
the reasoning. When your own placements report back -- when the laundromats beat
the gyms in your territory, or the apartment complexes disappoint -- edit those
numbers. That file is meant to be changed.

---

## Command reference

| Command | What it does |
|---|---|
| `mloc status` | What is in your database and when it was last refreshed |
| `mloc locations find` | Search and score placement prospects |
| `mloc locations list` | List stored prospects with filters |
| `mloc locations show <name>` | Full scoring breakdown for one site |
| `mloc locations categories` | Every site type and its priors |
| `mloc locations territories -n 4` | Split prospects into service areas |
| `mloc locations route --top 20` | Order stops into a driving run |
| `mloc routes find` | Poll every source for routes on the market |
| `mloc routes list` | List stored listings with filters |
| `mloc routes show <n>` | One listing in full, with price per machine |
| `mloc routes sources` | The configured sources |
| `mloc routes diagnose` | Why a source is or is not returning results |
| `mloc routes import <csv>` | Import a saved-search export |
| `mloc export sites\|listings` | CSV, GeoJSON or JSON |
| `mloc serve` | The browser view |

Useful flags: `--category`, `--min-score`, `--territory`, `--grade` on the
location commands; `--local-only`, `--min-relevance`, `--max-price`,
`--new-since-days` on the route commands. `--refresh` on `locations find`
bypasses the local Overpass cache.

Data lives in `~/.machine-locator/` (override with `--data-dir` or
`MACHINE_LOCATOR_HOME`). It is a plain SQLite file -- query it directly if you
want to.

---

## Development

```bash
pip install -e ".[dev]"
pytest
```

118 tests, no network access required: Overpass, HTTP and robots.txt are all
faked at the boundary, and the scrapers run against HTML fixtures -- including a
"the site got redesigned" fixture that proves the heuristic fallback works.

---

## Verification status

The offline logic is verified end to end: the full search → score → cluster →
route → export → serve path was exercised against a seeded Overpass cache, and
the web UI was rendered and screenshotted.

**What could not be verified:** the machine this was built on has outbound
network access blocked by policy, so no live request to Overpass or to any
listing site was ever made. That means:

- The **Overpass query syntax** is standard and the parsing is tested, but the
  first live `mloc locations find` will be the first real API call.
- The **listing source URLs and CSS selectors** in `sources.yaml` are marked
  `verify: true` where they need a browser check. Run `mloc routes diagnose`
  first -- it will tell you exactly which sources need a URL or selector fix,
  and the heuristic fallback means most will return something regardless.

Neither affects the CSV import path, which needs no network at all.

---

## Legal note

Scraping and robots.txt compliance are your responsibility as the operator of
this tool. The defaults are conservative -- robots.txt honoured, rate limited,
honest User-Agent, no retry on refusal -- and `--ignore-robots` is opt-in and
loudly announced. Site terms of service can prohibit automated access
independently of robots.txt; where a site blocks you, the saved-search-plus-CSV
path exists so you never need to fight it.

OpenStreetMap data is © OpenStreetMap contributors, available under the
[Open Database License](https://www.openstreetmap.org/copyright).

## License

MIT
