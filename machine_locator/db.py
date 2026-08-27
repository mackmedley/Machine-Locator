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

-- Where each prospect sits in your sales pipeline.
CREATE TABLE IF NOT EXISTS pipeline (
    site_id TEXT PRIMARY KEY,
    stage TEXT NOT NULL DEFAULT 'new',
    contact_name TEXT,
    contact_email TEXT,
    contact_phone TEXT,
    next_action TEXT,
    next_action_at TEXT,
    tags TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_pipeline_stage ON pipeline(stage);
CREATE INDEX IF NOT EXISTS idx_pipeline_next ON pipeline(next_action_at);

-- An append-only history of everything that happened with a prospect.
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT,
    body TEXT,
    created_at TEXT,
    meta TEXT
);
CREATE INDEX IF NOT EXISTS idx_activities_site ON activities(site_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_activities_time ON activities(created_at DESC);

-- Outreach messages: drafted, queued, sent, failed.
CREATE TABLE IF NOT EXISTS outreach_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'email',
    template_key TEXT,
    sequence_key TEXT,
    step INTEGER DEFAULT 0,
    to_address TEXT,
    subject TEXT,
    body TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    scheduled_at TEXT,
    sent_at TEXT,
    error TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_outreach_status ON outreach_messages(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_outreach_site ON outreach_messages(site_id, id DESC);

CREATE TABLE IF NOT EXISTS message_templates (
    key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'email',
    sequence_key TEXT,
    step INTEGER DEFAULT 0,
    delay_days INTEGER DEFAULT 0,
    subject TEXT,
    body TEXT,
    builtin INTEGER DEFAULT 0,
    updated_at TEXT
);

-- Addresses and sites that must never be contacted again.
CREATE TABLE IF NOT EXISTS suppression (
    value TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'email',
    reason TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT
);

-- Long-running work started from the web UI, polled for progress.
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    progress INTEGER DEFAULT 0,
    total INTEGER DEFAULT 0,
    message TEXT,
    error TEXT,
    result TEXT,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_recent ON jobs(id DESC);
"""

# Columns added after the first release. Each is applied only if missing, so an
# existing database upgrades in place instead of needing a rebuild.
MIGRATIONS = (
    ("sites", "email", "TEXT DEFAULT ''"),
    ("outreach_messages", "message_id", "TEXT DEFAULT ''"),
)

EXTRA_TABLES = """
CREATE TABLE IF NOT EXISTS handled_replies (
    message_id TEXT PRIMARY KEY,
    site_id TEXT,
    handled_at TEXT
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
        self.conn.executescript(EXTRA_TABLES)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after a database was first created."""
        for table, column, decl in MIGRATIONS:
            existing = {
                row["name"]
                for row in self.conn.execute(f"PRAGMA table_info({table})")
            }
            if column not in existing:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

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


# ---------------------------------------------------------------------------
# CRM, outreach and job storage
#
# These live as a mixin-style extension on Database so the original site and
# listing methods above stay readable. Everything here is keyed on site_id.
# ---------------------------------------------------------------------------

PIPELINE_STAGES = (
    ("new", "New"),
    ("queued", "Queued"),
    ("contacted", "Contacted"),
    ("following_up", "Following up"),
    ("interested", "Interested"),
    ("won", "Won"),
    ("lost", "Lost"),
)
STAGE_KEYS = tuple(key for key, _ in PIPELINE_STAGES)


def _extend(cls):
    """Attach the methods below to Database."""
    def decorator(func):
        setattr(cls, func.__name__, func)
        return func
    return decorator


# -------------------------------------------------------------- app settings

@_extend(Database)
def get_setting(self, key: str, default: str = "") -> str:
    row = self.conn.execute(
        "SELECT value FROM app_settings WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row and row["value"] is not None else default


@_extend(Database)
def set_setting(self, key: str, value: str) -> None:
    self.conn.execute(
        "INSERT OR REPLACE INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
        (key, value, utcnow()),
    )
    self.conn.commit()


@_extend(Database)
def all_settings(self) -> Dict[str, str]:
    return {r["key"]: r["value"] for r in self.conn.execute("SELECT * FROM app_settings")}


# ------------------------------------------------------------------ pipeline

@_extend(Database)
def get_pipeline(self, site_id: str) -> Dict[str, Any]:
    row = self.conn.execute(
        "SELECT * FROM pipeline WHERE site_id = ?", (site_id,)
    ).fetchone()
    if not row:
        return {"site_id": site_id, "stage": "new", "tags": []}
    data = dict(row)
    data["tags"] = _loads(data.get("tags"), [])
    return data


@_extend(Database)
def update_pipeline(self, site_id: str, **fields: Any) -> Dict[str, Any]:
    """Create or update a prospect's pipeline row. Unknown keys are ignored."""
    allowed = {
        "stage", "contact_name", "contact_email", "contact_phone",
        "next_action", "next_action_at", "tags",
    }
    current = self.get_pipeline(site_id)
    merged = {k: current.get(k) for k in allowed}
    for key, value in fields.items():
        if key in allowed:
            merged[key] = value
    if not merged.get("stage"):
        merged["stage"] = "new"
    merged["tags"] = json.dumps(merged.get("tags") or [])
    merged["site_id"] = site_id
    merged["updated_at"] = utcnow()
    columns = ", ".join(merged)
    placeholders = ", ".join(f":{k}" for k in merged)
    self.conn.execute(
        f"INSERT OR REPLACE INTO pipeline ({columns}) VALUES ({placeholders})", merged
    )
    self.conn.commit()
    return self.get_pipeline(site_id)


@_extend(Database)
def pipeline_counts(self) -> Dict[str, int]:
    """How many prospects sit in each stage, including the implicit 'new'."""
    counts = {key: 0 for key in STAGE_KEYS}
    for row in self.conn.execute("SELECT stage, COUNT(*) n FROM pipeline GROUP BY stage"):
        if row["stage"] in counts:
            counts[row["stage"]] = row["n"]
    total_sites = self.conn.execute("SELECT COUNT(*) c FROM sites").fetchone()["c"]
    tracked = self.conn.execute("SELECT COUNT(*) c FROM pipeline").fetchone()["c"]
    counts["new"] += max(0, total_sites - tracked)
    return counts


@_extend(Database)
def sites_in_stage(self, stage: str, limit: int = 200) -> List[Dict[str, Any]]:
    """Sites in one stage, joined with their pipeline row.

    'new' is special: it includes every scored site that has no pipeline row
    yet, so a fresh scan populates the board without a migration step.
    """
    if stage == "new":
        sql = """
            SELECT s.*, p.stage, p.contact_name, p.contact_email, p.contact_phone,
                   p.next_action, p.next_action_at, p.tags AS ptags
            FROM sites s LEFT JOIN pipeline p ON p.site_id = s.id
            WHERE p.site_id IS NULL OR p.stage = 'new'
            ORDER BY s.score DESC LIMIT ?
        """
        params: List[Any] = [limit]
    else:
        sql = """
            SELECT s.*, p.stage, p.contact_name, p.contact_email, p.contact_phone,
                   p.next_action, p.next_action_at, p.tags AS ptags
            FROM pipeline p JOIN sites s ON s.id = p.site_id
            WHERE p.stage = ?
            ORDER BY s.score DESC LIMIT ?
        """
        params = [stage, limit]
    return [_row_to_card(r) for r in self.conn.execute(sql, params)]


def _row_to_card(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    data["tags"] = _loads(data.pop("ptags", None), [])
    data["reasons"] = _loads(data.get("reasons"), [])
    data["breakdown"] = _loads(data.get("breakdown"), {})
    data.pop("tags_1", None)
    data["stage"] = data.get("stage") or "new"
    return data


# ---------------------------------------------------------------- activities

@_extend(Database)
def add_activity(
    self, site_id: str, kind: str, title: str = "", body: str = "",
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    self.conn.execute(
        "INSERT INTO activities (site_id, kind, title, body, created_at, meta) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (site_id, kind, title, body, utcnow(), json.dumps(meta or {})),
    )
    self.conn.commit()


@_extend(Database)
def site_activities(self, site_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    rows = self.conn.execute(
        "SELECT * FROM activities WHERE site_id = ? ORDER BY id DESC LIMIT ?",
        (site_id, limit),
    )
    out = []
    for row in rows:
        data = dict(row)
        data["meta"] = _loads(data.get("meta"), {})
        out.append(data)
    return out


@_extend(Database)
def recent_activities(self, limit: int = 30) -> List[Dict[str, Any]]:
    rows = self.conn.execute(
        "SELECT a.*, s.name AS site_name FROM activities a "
        "LEFT JOIN sites s ON s.id = a.site_id ORDER BY a.id DESC LIMIT ?",
        (limit,),
    )
    out = []
    for row in rows:
        data = dict(row)
        data["meta"] = _loads(data.get("meta"), {})
        out.append(data)
    return out


# ------------------------------------------------------------------ outreach

@_extend(Database)
def add_message(self, message: Dict[str, Any]) -> int:
    payload = {
        "site_id": message["site_id"],
        "channel": message.get("channel", "email"),
        "template_key": message.get("template_key", ""),
        "sequence_key": message.get("sequence_key", ""),
        "step": int(message.get("step", 0)),
        "to_address": message.get("to_address", ""),
        "subject": message.get("subject", ""),
        "body": message.get("body", ""),
        "status": message.get("status", "draft"),
        "scheduled_at": message.get("scheduled_at", ""),
        "sent_at": message.get("sent_at", ""),
        "error": message.get("error", ""),
        "message_id": message.get("message_id", ""),
        "created_at": utcnow(),
    }
    columns = ", ".join(payload)
    placeholders = ", ".join(f":{k}" for k in payload)
    cur = self.conn.execute(
        f"INSERT INTO outreach_messages ({columns}) VALUES ({placeholders})", payload
    )
    self.conn.commit()
    return int(cur.lastrowid)


@_extend(Database)
def update_message(self, row_id: int, **fields: Any) -> None:
    """Update one outreach row.

    The parameter is ``row_id``, not ``message_id``: ``message_id`` is a
    settable *column* holding the email's Message-ID header, and naming the
    primary key the same thing made the two collide.
    """
    allowed = {"status", "sent_at", "error", "scheduled_at", "subject", "body",
               "to_address", "message_id"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    assignments = ", ".join(f"{k} = :{k}" for k in updates)
    updates["id"] = row_id
    self.conn.execute(f"UPDATE outreach_messages SET {assignments} WHERE id = :id", updates)
    self.conn.commit()


@_extend(Database)
def get_message(self, message_id: int) -> Optional[Dict[str, Any]]:
    row = self.conn.execute(
        "SELECT m.*, s.name AS site_name FROM outreach_messages m "
        "LEFT JOIN sites s ON s.id = m.site_id WHERE m.id = ?",
        (message_id,),
    ).fetchone()
    return dict(row) if row else None


@_extend(Database)
def query_messages(
    self, status: Optional[str] = None, site_id: Optional[str] = None,
    due_only: bool = False, limit: int = 200,
) -> List[Dict[str, Any]]:
    sql = ("SELECT m.*, s.name AS site_name, s.address AS site_address "
           "FROM outreach_messages m LEFT JOIN sites s ON s.id = m.site_id WHERE 1=1")
    params: List[Any] = []
    if status:
        sql += " AND m.status = ?"
        params.append(status)
    if site_id:
        sql += " AND m.site_id = ?"
        params.append(site_id)
    if due_only:
        sql += " AND (m.scheduled_at = '' OR m.scheduled_at IS NULL OR m.scheduled_at <= ?)"
        params.append(utcnow())
    # For a queue, the useful order is "what goes out next"; for history it is
    # "what happened last".
    if status == "queued":
        sql += " ORDER BY m.scheduled_at ASC, m.id ASC LIMIT ?"
    else:
        sql += " ORDER BY m.id DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in self.conn.execute(sql, params)]


@_extend(Database)
def messages_sent_since(self, iso_time: str) -> int:
    row = self.conn.execute(
        "SELECT COUNT(*) c FROM outreach_messages WHERE status = 'sent' AND sent_at >= ?",
        (iso_time,),
    ).fetchone()
    return int(row["c"])


@_extend(Database)
def outreach_daily_counts(self, days: int = 30) -> List[Dict[str, Any]]:
    """Sent-per-day, for the dashboard trend line."""
    rows = self.conn.execute(
        "SELECT substr(sent_at, 1, 10) AS day, COUNT(*) AS n "
        "FROM outreach_messages WHERE status = 'sent' AND sent_at != '' "
        "GROUP BY day ORDER BY day DESC LIMIT ?",
        (days,),
    )
    return [dict(r) for r in rows][::-1]


@_extend(Database)
def has_been_contacted(self, site_id: str) -> bool:
    row = self.conn.execute(
        "SELECT 1 FROM outreach_messages WHERE site_id = ? AND status IN ('sent','queued') LIMIT 1",
        (site_id,),
    ).fetchone()
    return row is not None


# --------------------------------------------------------------- suppression

@_extend(Database)
def suppress(self, value: str, kind: str = "email", reason: str = "") -> None:
    self.conn.execute(
        "INSERT OR REPLACE INTO suppression (value, kind, reason, created_at) "
        "VALUES (?, ?, ?, ?)",
        (value.strip().lower(), kind, reason, utcnow()),
    )
    self.conn.commit()


@_extend(Database)
def unsuppress(self, value: str) -> None:
    self.conn.execute("DELETE FROM suppression WHERE value = ?", (value.strip().lower(),))
    self.conn.commit()


@_extend(Database)
def suppression_list(self) -> List[Dict[str, Any]]:
    return [dict(r) for r in self.conn.execute(
        "SELECT * FROM suppression ORDER BY created_at DESC"
    )]


@_extend(Database)
def is_suppressed(self, email: str = "", site_id: str = "") -> bool:
    """True if this address, its domain, or this site is on the do-not-contact list."""
    candidates = []
    if email:
        address = email.strip().lower()
        candidates.append(address)
        if "@" in address:
            candidates.append(address.split("@", 1)[1])
    if site_id:
        candidates.append(site_id.strip().lower())
    if not candidates:
        return False
    marks = ", ".join("?" for _ in candidates)
    row = self.conn.execute(
        f"SELECT 1 FROM suppression WHERE value IN ({marks}) LIMIT 1", candidates
    ).fetchone()
    return row is not None


# ----------------------------------------------------------------- templates

@_extend(Database)
def upsert_template(self, template: Dict[str, Any]) -> None:
    payload = {
        "key": template["key"],
        "name": template.get("name", template["key"]),
        "channel": template.get("channel", "email"),
        "sequence_key": template.get("sequence_key", ""),
        "step": int(template.get("step", 0)),
        "delay_days": int(template.get("delay_days", 0)),
        "subject": template.get("subject", ""),
        "body": template.get("body", ""),
        "builtin": 1 if template.get("builtin") else 0,
        "updated_at": utcnow(),
    }
    columns = ", ".join(payload)
    placeholders = ", ".join(f":{k}" for k in payload)
    self.conn.execute(
        f"INSERT OR REPLACE INTO message_templates ({columns}) VALUES ({placeholders})",
        payload,
    )
    self.conn.commit()


@_extend(Database)
def get_template(self, key: str) -> Optional[Dict[str, Any]]:
    row = self.conn.execute(
        "SELECT * FROM message_templates WHERE key = ?", (key,)
    ).fetchone()
    return dict(row) if row else None


@_extend(Database)
def list_templates(self, channel: Optional[str] = None) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM message_templates"
    params: List[Any] = []
    if channel:
        sql += " WHERE channel = ?"
        params.append(channel)
    # The email sequence is the main flow, so it leads; standalone scripts
    # (which carry no sequence_key) come after it.
    sql += " ORDER BY (sequence_key = '') ASC, sequence_key, step, name"
    return [dict(r) for r in self.conn.execute(sql, params)]


@_extend(Database)
def delete_template(self, key: str) -> None:
    self.conn.execute("DELETE FROM message_templates WHERE key = ? AND builtin = 0", (key,))
    self.conn.commit()


# ---------------------------------------------------------------------- jobs

@_extend(Database)
def create_job(self, kind: str, message: str = "") -> int:
    cur = self.conn.execute(
        "INSERT INTO jobs (kind, status, message, started_at) VALUES (?, 'queued', ?, ?)",
        (kind, message, utcnow()),
    )
    self.conn.commit()
    return int(cur.lastrowid)


@_extend(Database)
def update_job(self, job_id: int, **fields: Any) -> None:
    allowed = {"status", "progress", "total", "message", "error", "result", "finished_at"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    if isinstance(updates.get("result"), (dict, list)):
        updates["result"] = json.dumps(updates["result"])
    assignments = ", ".join(f"{k} = :{k}" for k in updates)
    updates["id"] = job_id
    self.conn.execute(f"UPDATE jobs SET {assignments} WHERE id = :id", updates)
    self.conn.commit()


@_extend(Database)
def get_job(self, job_id: int) -> Optional[Dict[str, Any]]:
    row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["result"] = _loads(data.get("result"), {})
    return data


@_extend(Database)
def recent_jobs(self, limit: int = 10) -> List[Dict[str, Any]]:
    rows = self.conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))
    out = []
    for row in rows:
        data = dict(row)
        data["result"] = _loads(data.get("result"), {})
        out.append(data)
    return out


@_extend(Database)
def active_job(self) -> Optional[Dict[str, Any]]:
    row = self.conn.execute(
        "SELECT * FROM jobs WHERE status IN ('queued','running') ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    data = dict(row)
    data["result"] = _loads(data.get("result"), {})
    return data


# ------------------------------------------------------------- dashboard agg

@_extend(Database)
def grade_counts(self) -> Dict[str, int]:
    counts = {g: 0 for g in ("A+", "A", "B", "C", "D")}
    for row in self.conn.execute(
        "SELECT grade, COUNT(*) n FROM sites WHERE grade != '' GROUP BY grade"
    ):
        if row["grade"] in counts:
            counts[row["grade"]] = row["n"]
    return counts


@_extend(Database)
def outreach_stats(self) -> Dict[str, int]:
    def count(where: str, params: tuple = ()) -> int:
        return int(self.conn.execute(
            f"SELECT COUNT(*) c FROM outreach_messages WHERE {where}", params
        ).fetchone()["c"])

    return {
        "draft": count("status = 'draft'"),
        "queued": count("status = 'queued'"),
        "sent": count("status = 'sent'"),
        "failed": count("status = 'failed'"),
    }


# -------------------------------------------------------------- reply matching

@_extend(Database)
def find_sent_by_message_id(self, message_id: str) -> Optional[Dict[str, Any]]:
    """The outgoing message a reply is answering, matched on Message-ID."""
    clean = (message_id or "").strip()
    if not clean:
        return None
    row = self.conn.execute(
        "SELECT * FROM outreach_messages WHERE message_id = ? AND message_id != '' LIMIT 1",
        (clean,),
    ).fetchone()
    return dict(row) if row else None


@_extend(Database)
def find_sent_by_recipient(self, address: str) -> Optional[Dict[str, Any]]:
    """Fallback match: the most recent thing we sent to this address.

    Plenty of mail clients drop or rewrite In-Reply-To, so matching on the
    address is what makes reply detection work in practice.
    """
    clean = (address or "").strip().lower()
    if not clean:
        return None
    row = self.conn.execute(
        "SELECT * FROM outreach_messages WHERE lower(to_address) = ? AND status = 'sent' "
        "ORDER BY id DESC LIMIT 1",
        (clean,),
    ).fetchone()
    return dict(row) if row else None


@_extend(Database)
def is_reply_handled(self, message_id: str) -> bool:
    if not message_id:
        return False
    return self.conn.execute(
        "SELECT 1 FROM handled_replies WHERE message_id = ?", (message_id,)
    ).fetchone() is not None


@_extend(Database)
def mark_reply_handled(self, message_id: str, site_id: str = "") -> None:
    if not message_id:
        return
    self.conn.execute(
        "INSERT OR REPLACE INTO handled_replies (message_id, site_id, handled_at) "
        "VALUES (?, ?, ?)",
        (message_id, site_id, utcnow()),
    )
    self.conn.commit()


# ------------------------------------------------------------------- today

@_extend(Database)
def due_actions(self, limit: int = 40) -> List[Dict[str, Any]]:
    """Prospects with something owed on them today or already overdue.

    The outreach queue answers "what will the machine send"; this answers
    "what do *I* need to do", which is a different and more useful question
    once a pipeline has anything in it.
    """
    rows = self.conn.execute(
        "SELECT p.site_id, p.stage, p.next_action, p.next_action_at, "
        "       p.contact_name, p.contact_phone, p.contact_email, "
        "       s.name, s.address, s.score, s.grade, s.phone "
        "FROM pipeline p JOIN sites s ON s.id = p.site_id "
        "WHERE p.next_action_at != '' AND p.next_action_at IS NOT NULL "
        # 'queued' is excluded on purpose: an intro email waiting to send is
        # work for the sender, not for the person. It is already counted as
        # "emails ready to go out", and listing it twice buries the calls.
        "  AND p.next_action_at <= ? AND p.stage NOT IN ('won', 'lost', 'queued') "
        "ORDER BY p.next_action_at ASC LIMIT ?",
        (utcnow(), limit),
    )
    return [dict(r) for r in rows]


@_extend(Database)
def awaiting_reply_followup(self, limit: int = 20) -> List[Dict[str, Any]]:
    """People who answered and are still sitting in Interested.

    A warm reply going cold because nobody followed up is the most expensive
    thing that can happen in this pipeline, so it gets its own list.
    """
    rows = self.conn.execute(
        "SELECT p.site_id, p.contact_name, p.contact_email, "
        "       s.name, s.address, s.score, s.grade, s.phone, p.updated_at "
        "FROM pipeline p JOIN sites s ON s.id = p.site_id "
        "WHERE p.stage = 'interested' ORDER BY p.updated_at ASC LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in rows]
