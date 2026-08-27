"""Runtime configuration and shared defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Oklahoma City proper is a sprawling ~600 sq mi city limit. The bounding box
# below covers the city limits plus the inner metro (Edmond, Moore, Del City,
# Midwest City, Yukon, Bethany) because vending routes rarely stop at the
# municipal line.
OKC_BBOX = (35.24, -97.90, 35.75, -97.10)  # south, west, north, east
OKC_CENTER = (35.4676, -97.5164)

DEFAULT_CITY = "Oklahoma City"
DEFAULT_STATE = "Oklahoma"

# Overpass mirrors, tried in order. The public instances are rate limited;
# be a good citizen and cache results in the local database.
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
)

USER_AGENT = (
    "machine-locator/0.1 (vending placement research; "
    "https://github.com/mackmedley/machine-locator)"
)


# Filled in on first run so the app opens already configured. Change any of it
# in Settings -- these are only the starting values, and they are never
# re-applied once a setting exists.
SEED_SETTINGS = {
    "business_name": "Blue Ox Vending",
    "business_phone": "405-397-2784",
    "city": DEFAULT_CITY,
    "daily_send_cap": "40",
    # Only ever used if a prospect asks what they get out of it -- the pitch
    # leads with "free and no hassle", never with a cut of the sales.
    "commission_line": "a share of the sales, agreed in writing before anything goes in",
}


def default_data_dir() -> Path:
    """Where the SQLite database and exports live."""
    env = os.environ.get("MACHINE_LOCATOR_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".machine-locator"


@dataclass
class Settings:
    """Everything the pipelines need to run, in one object."""

    data_dir: Path = field(default_factory=default_data_dir)
    city: str = DEFAULT_CITY
    state: str = DEFAULT_STATE
    bbox: tuple = OKC_BBOX
    center: tuple = OKC_CENTER

    # Politeness / networking
    request_timeout: int = 45
    overpass_timeout: int = 180
    rate_limit_seconds: float = 2.0
    respect_robots: bool = True
    user_agent: str = USER_AGENT

    # Scoring tunables
    competition_radius_m: float = 400.0
    saturation_radius_m: float = 120.0
    route_density_radius_m: float = 1500.0

    # Optional enrichment
    google_places_key: str = field(
        default_factory=lambda: os.environ.get("GOOGLE_PLACES_API_KEY", "")
    )

    @property
    def db_path(self) -> Path:
        return self.data_dir / "machine_locator.sqlite3"

    @property
    def export_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.export_dir, self.cache_dir):
            path.mkdir(parents=True, exist_ok=True)
