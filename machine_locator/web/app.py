"""Flask app: a map of placement prospects and a browser for routes on sale.

Runs locally against the same SQLite file the CLI writes, so anything you have
already scanned shows up immediately.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, render_template, request

from ..config import Settings
from ..db import Database
from ..locations.categories import CATEGORY_SPECS
from ..locations.scoring import machine_recommendation


def create_app(settings: Optional[Settings] = None) -> Flask:
    settings = settings or Settings()
    settings.ensure_dirs()
    app = Flask(__name__)
    app.config["SETTINGS"] = settings

    def db() -> Database:
        return Database(settings.db_path)

    @app.route("/")
    def index() -> str:
        with db() as database:
            stats = database.stats()
        return render_template(
            "index.html",
            city=settings.city,
            center=settings.center,
            stats=stats,
            categories=sorted(
                [{"key": s.key, "label": s.label} for s in CATEGORY_SPECS],
                key=lambda c: c["label"],
            ),
        )

    @app.route("/listings")
    def listings_page() -> str:
        with db() as database:
            stats = database.stats()
        return render_template("listings.html", city=settings.city, stats=stats)

    @app.route("/api/sites")
    def api_sites() -> Any:
        min_score = request.args.get("min_score", default=0.0, type=float)
        category = request.args.get("category") or None
        limit = request.args.get("limit", default=2000, type=int)
        search = request.args.get("search") or None
        with db() as database:
            sites = database.query_sites(
                limit=limit, min_score=min_score, category=category, search=search
            )
            notes = database.get_site_notes()

        payload: List[Dict[str, Any]] = []
        for site in sites:
            row = site.to_dict()
            row["sell_here"] = machine_recommendation(site)
            row["note"] = notes.get(site.id, {})
            payload.append(row)
        return jsonify({"count": len(payload), "sites": payload})

    @app.route("/api/listings")
    def api_listings() -> Any:
        local_only = request.args.get("local_only") == "1"
        min_relevance = request.args.get("min_relevance", default=0.0, type=float)
        max_price = request.args.get("max_price", type=float)
        limit = request.args.get("limit", default=300, type=int)
        with db() as database:
            listings = database.query_listings(
                limit=limit, min_relevance=min_relevance,
                local_only=local_only, max_price=max_price,
            )
        return jsonify({
            "count": len(listings),
            "listings": [l.to_dict() for l in listings],
        })

    @app.route("/api/sites/<path:site_id>/note", methods=["POST"])
    def api_set_note(site_id: str) -> Any:
        """Track your own pipeline: contacted / pitched / won / dead."""
        data = request.get_json(silent=True) or {}
        with db() as database:
            database.set_site_note(
                site_id,
                status=str(data.get("status", ""))[:40],
                note=str(data.get("note", ""))[:2000],
            )
        return jsonify({"ok": True})

    return app


def main() -> None:  # pragma: no cover
    create_app().run(debug=True)


if __name__ == "__main__":  # pragma: no cover
    main()
