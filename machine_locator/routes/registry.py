"""Loads source definitions from YAML and instantiates them."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml

from .base import Source
from .html_source import HtmlSource
from .rss_source import RssSource

DEFAULT_SOURCES_FILE = Path(__file__).with_name("sources.yaml")

_TYPES = {"rss": RssSource, "html": HtmlSource}


def load_source_configs(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Read source definitions.

    A ``sources.yaml`` in the user's data directory or working directory takes
    precedence over the packaged defaults, so local fixes survive upgrades.
    """
    config_path = Path(path) if path else DEFAULT_SOURCES_FILE
    if not config_path.exists():
        raise FileNotFoundError(f"no source config at {config_path}")
    data = yaml.safe_load(config_path.read_text()) or {}
    return list(data.get("sources", []))


def build_sources(
    path: Optional[Path] = None,
    only: Optional[Sequence[str]] = None,
    include_disabled: bool = False,
) -> List[Source]:
    sources: List[Source] = []
    for config in load_source_configs(path):
        name = config.get("name")
        if not name:
            continue
        if only and name not in only:
            continue
        # An explicitly requested source is run even if it is disabled by default.
        if not config.get("enabled", True) and not include_disabled and not only:
            continue
        source_type = str(config.get("type", "html"))
        cls = _TYPES.get(source_type)
        if cls is None:
            raise ValueError(
                f"source '{name}' has unknown type '{source_type}' "
                f"(expected one of {', '.join(sorted(_TYPES))})"
            )
        sources.append(cls(config))
    return sources


def source_names(path: Optional[Path] = None) -> List[str]:
    return [str(c.get("name")) for c in load_source_configs(path) if c.get("name")]
