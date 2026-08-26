"""SQLite persistence.

Two things matter here beyond plain storage:

1. Re-running a search must not duplicate rows, so everything upserts on a
   stable id.
2. ``first_seen`` is preserved across upserts while ``last_seen`` moves. That is
   what makes "show me route listings that appeared since Tuesday" possible,
   which is the whole point of tracking a market that turns over weekly.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .models import RouteListing, Site, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    category_label TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    address TEXT,
    phone TEXT,
    website TEXT,
    opening_hours TEXT,
    tags TEXT,
    score REAL,
    grade TEXT,
    breakdown TEXT,
    reasons TEXT,
    competitors_nearby INTEGER,
    vending_nearby INTEGER,
    neighbors_nearby INTEGER,
    territory INTEGER,
    source TEXT,
    first_seen TEXT,
    last_seen TEXT
);
CREATE INDEX IF NOT EXISTS idx_sites_score ON sites(score DESC);
CREATE INDEX IF NOT EXISTS idx_sites_category ON sites(category);

CREATE TABLE IF NOT EXISTS route_listings (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT,
    price REAL,
    price_text TEXT,
    cash_flow REAL,
    gross_revenue REAL,
    machine_count INTEGER,
    location_text TEXT,
    state TEXT,
    description TEXT,
    posted_at TEXT,
    relevance REAL,
    relevance_reasons TEXT,
    is_local INTEGER,
    raw TEXT,
    first_seen TEXT,
    last_seen TEXT
);
CREATE INDEX IF NOT EXISTS idx_listings_seen ON route_listings(first_seen DESC);
CREATE INDEX IF NOT EXISTS idx_listings_relevance ON route_listings(relevance DESC);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    found INTEGER,
    new_items INTEGER,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS site_notes (
    site_id TEXT PRIMARY KEY,
    status TEXT,
    note TEXT,
    updated_at TEXT
);
"""

_JSON_SITE_FIELDS = ("tags", "breakdown", "reasons")
_JSON_LISTING_FIELDS = ("relevance_reasons", "raw")


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---------------------------------------------------------------- sites

    def upsert_sites(self, sites: Iterable[Site]) -> int:
        """Insert or refresh sites. Returns the count of genuinely new rows."""
        new_count = 0
        cur = self.conn.cursor()
        for site in sites:
            existing = cur.execute(
                "SELECT first_seen FROM sites WHERE id = ?", (site.id,)
            ).fetchone()
            if existing:
                site.first_seen = existing["first_seen"]
            else:
                new_count += 1
            site.last_seen = utcnow()
            payload = site.to_dict()
            for key in _JSON_SITE_FIELDS:
                payload[key] = json.dumps(payload[key])
            columns = ", ".join(payload.keys())
            placeholders = ", ".join(f":{k}" for k in payload)
            cur.execute(
                f"INSERT OR REPLACE INTO sites ({columns}) VALUES ({placeholders})",
                payload,
            )
        self.conn.commit()
        return new_count

    def query_sites(
        self,
        limit: int = 100,
        min_score: float = 0.0,
        category: Optional[str] = None,
        territory: Optional[int] = None,
        search: Optional[str] = None,
    ) -> List[Site]:
        sql = "SELECT * FROM sites WHERE score >= ?"
        params: List[Any] = [min_score]
        if category:
            sql += " AND category = ?"
            params.append(category)
        if territory is not None:
            sql += " AND territory = ?"
            params.append(territory)
        if search:
            sql += " AND (name LIKE ? OR address LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%"])
        sql += " ORDER BY score DESC LIMIT ?"
        params.append(limit)
        return [_row_to_site(r) for r in self.conn.execute(sql, params)]

    def site_categories(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT category, category_label, COUNT(*) AS n, AVG(score) AS avg_score "
            "FROM sites GROUP BY category ORDER BY avg_score DESC"
        )
        return [dict(r) for r in rows]

    def update_site_territories(self, assignments: Dict[str, int]) -> None:
        self.conn.executemany(
            "UPDATE sites SET territory = ? WHERE id = ?",
            [(t, sid) for sid, t in assignments.items()],
        )
        self.conn.commit()

    def set_site_note(self, site_id: str, status: str, note: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO site_notes (site_id, status, note, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (site_id, status, note, utcnow()),
        )
        self.conn.commit()

    def get_site_notes(self) -> Dict[str, Dict[str, str]]:
        return {
            r["site_id"]: {"status": r["status"], "note": r["note"], "updated_at": r["updated_at"]}
            for r in self.conn.execute("SELECT * FROM site_notes")
        }

    # ------------------------------------------------------------- listings

    def upsert_listings(self, listings: Iterable[RouteListing]) -> List[RouteListing]:
        """Upsert listings, returning only the ones never seen before."""
        fresh: List[RouteListing] = []
        cur = self.conn.cursor()
        for listing in listings:
            existing = cur.execute(
                "SELECT first_seen FROM route_listings WHERE id = ?", (listing.id,)
            ).fetchone()
            if existing:
                listing.first_seen = existing["first_seen"]
            else:
                fresh.append(listing)
            listing.last_seen = utcnow()
            payload = listing.to_dict()
            for key in _JSON_LISTING_FIELDS:
                payload[key] = json.dumps(payload[key])
            payload["is_local"] = 1 if payload["is_local"] else 0
            columns = ", ".join(payload.keys())
            placeholders = ", ".join(f":{k}" for k in payload)
            cur.execute(
                f"INSERT OR REPLACE INTO route_listings ({columns}) VALUES ({placeholders})",
                payload,
            )
        self.conn.commit()
        return fresh

    def query_listings(
        self,
        limit: int = 100,
        min_relevance: float = 0.0,
        source: Optional[str] = None,
        local_only: bool = False,
        since: Optional[str] = None,
        max_price: Optional[float] = None,
    ) -> List[RouteListing]:
        sql = "SELECT * FROM route_listings WHERE relevance >= ?"
        params: List[Any] = [min_relevance]
        if source:
            sql += " AND source = ?"
            params.append(source)
        if local_only:
            sql += " AND is_local = 1"
        if since:
            sql += " AND first_seen >= ?"
            params.append(since)
        if max_price is not None:
            sql += " AND price IS NOT NULL AND price <= ?"
            params.append(max_price)
        sql += " ORDER BY relevance DESC, first_seen DESC LIMIT ?"
        params.append(limit)
        return [_row_to_listing(r) for r in self.conn.execute(sql, params)]

    # ------------------------------------------------------------------ runs

    def record_run(
        self, kind: str, started_at: str, found: int, new_items: int, notes: str = ""
    ) -> None:
        self.conn.execute(
            "INSERT INTO runs (kind, started_at, finished_at, found, new_items, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (kind, started_at, utcnow(), found, new_items, notes),
        )
        self.conn.commit()

    def recent_runs(self, limit: int = 10) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]

    def stats(self) -> Dict[str, Any]:
        def scalar(sql: str) -> Any:
            row = self.conn.execute(sql).fetchone()
            return row[0] if row else 0

        return {
            "sites": scalar("SELECT COUNT(*) FROM sites"),
            "sites_a_grade": scalar("SELECT COUNT(*) FROM sites WHERE score >= 75"),
            "listings": scalar("SELECT COUNT(*) FROM route_listings"),
            "listings_local": scalar("SELECT COUNT(*) FROM route_listings WHERE is_local = 1"),
            "last_site_run": scalar(
                "SELECT finished_at FROM runs WHERE kind = 'locations' ORDER BY id DESC LIMIT 1"
            ),
            "last_route_run": scalar(
                "SELECT finished_at FROM runs WHERE kind = 'routes' ORDER BY id DESC LIMIT 1"
            ),
        }


def _loads(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return fallback


def _row_to_site(row: sqlite3.Row) -> Site:
    data = dict(row)
    data["tags"] = _loads(data.get("tags"), {})
    data["breakdown"] = _loads(data.get("breakdown"), {})
    data["reasons"] = _loads(data.get("reasons"), [])
    return Site(**data)


def _row_to_listing(row: sqlite3.Row) -> RouteListing:
    data = dict(row)
    data["relevance_reasons"] = _loads(data.get("relevance_reasons"), [])
    data["raw"] = _loads(data.get("raw"), {})
    data["is_local"] = bool(data.get("is_local"))
    return RouteListing(**data)
