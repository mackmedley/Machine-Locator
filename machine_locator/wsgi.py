"""WSGI entry point for running behind a real web server.

    gunicorn 'machine_locator.wsgi:app' --workers 1 --threads 4

One worker with threads, not several workers: background scans and sends run as
threads inside the process that started them, and the SQLite database is a
single file. Several worker processes would each hold their own job runner and
contend for the same file for no benefit -- this app serves one operator.
"""

from __future__ import annotations

import os

from .config import Settings
from .web.app import create_app


def build() -> "object":
    settings = Settings()
    city = os.environ.get("MACHINE_LOCATOR_CITY", "").strip()
    if city:
        settings.city = city
    settings.ensure_dirs()
    # Anything served through WSGI is reachable from outside the machine, so a
    # password is mandatory -- the first open asks for one.
    return create_app(settings, public=True)


app = build()
