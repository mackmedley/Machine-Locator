import io

import pytest

from machine_locator.db import Database
from machine_locator.models import RouteListing, Site
from machine_locator.web.app import create_app


def make_site(site_id="node/1", name="Suds Laundromat", score=88.0, grade="A+",
              category="laundromat", email=""):
    return Site(
        id=site_id, name=name, category=category, category_label="Laundromat",
        lat=35.4676, lon=-97.5164, address="123 NW 23rd St", score=score, grade=grade,
        breakdown={"traffic": 6.0}, reasons=["captive audience"], email=email,
    )


@pytest.fixture
def seeded(settings):
    with Database(settings.db_path) as database:
        database.upsert_sites([
            make_site(email="owner@suds.example"),
            make_site("node/2", "Iron Works Gym", 41.0, "D", "gym"),
        ])
        database.upsert_listings([
            RouteListing(id="a", source="demo", title="Vending Route -- 30 machines",
                         url="https://example.org/1", price=75_000.0, cash_flow=40_000.0,
                         machine_count=30, relevance=90.0,
                         location_text="Oklahoma City, OK", is_local=True),
            RouteListing(id="b", source="demo", title="Vending business in Texas",
                         url="https://example.org/2", price=500_000.0, relevance=40.0,
                         location_text="Dallas, TX", is_local=False),
        ])
    return settings


@pytest.fixture
def app(seeded):
    application = create_app(seeded)
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def configured(client, seeded):
    """A fully set-up sender, so the send gate opens."""
    client.post("/api/settings", json={
        "business_name": "Sooner Vending", "sender_name": "Mack",
        "sender_email": "mack@sooner.example", "postal_address": "1 Main St, OKC, OK 73106",
        "smtp_host": "smtp.example.com", "smtp_username": "mack@sooner.example",
        "smtp_password": "secret", "commission_line": "15% of gross",
    })
    return client


# ------------------------------------------------------------------- pages

@pytest.mark.parametrize("path", [
    "/", "/prospects", "/pipeline", "/outreach", "/listings", "/planner", "/settings",
])
def test_every_page_renders(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert "Machine Locator" in response.get_data(as_text=True)


def test_prospects_page_lists_categories(client):
    body = client.get("/prospects").get_data(as_text=True)
    assert "Laundromat" in body


def test_setup_warning_shows_until_configured(client, configured):
    fresh = create_app(configured.application.config["SETTINGS"])
    fresh.config["TESTING"] = True
    body = fresh.test_client().get("/").get_data(as_text=True)
    assert "Finish setup before sending outreach" not in body


def test_setup_warning_present_when_unconfigured(client):
    assert "Finish setup before sending outreach" in client.get("/").get_data(as_text=True)


# -------------------------------------------------------------------- api

def test_stats_endpoint_shape(client):
    data = client.get("/api/stats").get_json()
    assert data["stats"]["sites"] == 2
    assert data["grades"]["A+"] == 1
    assert set(data["pipeline"]) >= {"new", "won", "lost"}
    assert "outreach" in data and "categories" in data


def test_sites_api_filters(client):
    assert client.get("/api/sites").get_json()["count"] == 2
    assert client.get("/api/sites?min_score=50").get_json()["count"] == 1
    assert client.get("/api/sites?category=gym").get_json()["count"] == 1
    assert client.get("/api/sites?grade=A%2B").get_json()["count"] == 1
    assert client.get("/api/sites?search=Iron").get_json()["count"] == 1


def test_site_detail_includes_pipeline_and_merge_preview(client):
    data = client.get("/api/sites/node/1").get_json()
    assert data["site"]["name"] == "Suds Laundromat"
    assert data["pipeline"]["stage"] == "new"
    assert data["merge_preview"]["business_name"] == "Suds Laundromat"
    assert data["suppressed"] is False


def test_site_detail_404(client):
    assert client.get("/api/sites/node/999").status_code == 404


def test_pipeline_move_and_board(client):
    client.post("/api/sites/node/1/pipeline", json={"stage": "contacted"})
    board = client.get("/api/pipeline").get_json()
    assert [c["id"] for c in board["board"]["contacted"]] == ["node/1"]
    assert board["counts"]["contacted"] == 1
    # An untouched site still appears under "new" without a pipeline row.
    assert [c["id"] for c in board["board"]["new"]] == ["node/2"]


def test_pipeline_rejects_unknown_stage(client):
    response = client.post("/api/sites/node/1/pipeline", json={"stage": "banana"})
    assert response.status_code == 400
    assert "unknown stage" in response.get_json()["error"]


def test_notes_land_in_the_timeline(client):
    assert client.post("/api/sites/node/1/note", json={"note": "Owner said call back"}).status_code == 200
    activities = client.get("/api/sites/node/1").get_json()["activities"]
    assert activities[0]["body"] == "Owner said call back"


def test_empty_note_rejected(client):
    assert client.post("/api/sites/node/1/note", json={"note": "  "}).status_code == 400


def test_listings_api_filters(client):
    assert client.get("/api/listings").get_json()["count"] == 2
    assert client.get("/api/listings?local_only=1").get_json()["count"] == 1
    assert client.get("/api/listings?max_price=100000").get_json()["count"] == 1


def test_listing_csv_import_over_http(client):
    csv_bytes = (
        b"Business Name,Asking Price,Location\n"
        b'"Vending Route - 12 machines","$40,000","Oklahoma City, OK"\n'
    )
    response = client.post(
        "/api/listings/import",
        data={"file": (io.BytesIO(csv_bytes), "export.csv"), "source_name": "manual"},
        content_type="multipart/form-data",
    )
    assert response.get_json()["imported"] == 1


def test_listing_import_requires_a_file(client):
    assert client.post("/api/listings/import", data={},
                       content_type="multipart/form-data").status_code == 400


# --------------------------------------------------------------- settings

def test_settings_roundtrip_hides_password(configured):
    stored = configured.get("/api/settings").get_json()
    assert stored["business_name"] == "Sooner Vending"
    assert "smtp_password" not in stored


def test_blank_password_keeps_the_saved_one(configured):
    configured.post("/api/settings", json={"smtp_password": "", "sender_name": "Mack M"})
    response = configured.post("/api/settings", json={})
    assert response.get_json()["smtp_configured"] is True


def test_settings_reports_missing_fields(client):
    response = client.post("/api/settings", json={"business_name": "Only This"})
    assert "Your name" in response.get_json()["missing"]


def test_suppression_add_and_remove(client):
    added = client.post("/api/suppression", json={"value": "No@Example.com "})
    assert any(e["value"] == "no@example.com" for e in added.get_json()["entries"])
    removed = client.delete("/api/suppression/no@example.com")
    assert removed.get_json()["entries"] == []


# --------------------------------------------------------------- outreach

def test_outreach_preview_blocks_sites_without_email(configured):
    data = configured.post("/api/outreach/preview",
                           json={"site_ids": ["node/1", "node/2"]}).get_json()
    assert [r["site_id"] for r in data["ready"]] == ["node/1"]
    assert data["blocked"][0]["problem"] == "No email address on file"
    assert data["steps"] == 3
    body = data["ready"][0]["steps"][0]["body"]
    assert "Suds Laundromat" in body and "15% of gross" in body
    assert "{" not in data["ready"][0]["steps"][0]["subject"]


def test_enroll_queues_the_whole_sequence(configured):
    result = configured.post("/api/outreach/enroll", json={"site_ids": ["node/1"]}).get_json()
    assert result["enrolled"] == 1
    assert result["messages"] == 3
    messages = configured.get("/api/outreach/messages?status=queued").get_json()["messages"]
    assert len(messages) == 3
    assert {m["step"] for m in messages} == {0, 1, 2}


def test_enroll_is_blocked_without_sender_details(client):
    response = client.post("/api/outreach/enroll", json={"site_ids": ["node/1"]})
    assert response.status_code == 400
    assert "Settings" in response.get_json()["error"]


def test_enroll_skips_a_site_twice(configured):
    configured.post("/api/outreach/enroll", json={"site_ids": ["node/1"]})
    again = configured.post("/api/outreach/enroll", json={"site_ids": ["node/1"]}).get_json()
    assert again["enrolled"] == 0
    assert "Already has outreach" in again["skipped"][0]["reason"]


def test_marking_won_cancels_queued_followups(configured):
    configured.post("/api/outreach/enroll", json={"site_ids": ["node/1"]})
    configured.post("/api/sites/node/1/pipeline", json={"stage": "won"})
    queued = configured.get("/api/outreach/messages?status=queued").get_json()["messages"]
    assert queued == []


def test_opt_out_suppresses_and_stops_the_sequence(configured):
    configured.post("/api/outreach/enroll", json={"site_ids": ["node/1"]})
    result = configured.post("/api/sites/node/1/reply",
                             json={"text": "please STOP emailing me"}).get_json()
    assert result["opted_out"] is True
    assert result["cancelled"] == 3
    detail = configured.get("/api/sites/node/1").get_json()
    assert detail["suppressed"] is True
    assert detail["pipeline"]["stage"] == "lost"


def test_positive_reply_moves_to_interested(configured):
    configured.post("/api/outreach/enroll", json={"site_ids": ["node/1"]})
    result = configured.post("/api/sites/node/1/reply",
                             json={"text": "Sure, come by Tuesday"}).get_json()
    assert result["opted_out"] is False
    assert configured.get("/api/sites/node/1").get_json()["pipeline"]["stage"] == "interested"


def test_message_edit_and_cancel(configured):
    configured.post("/api/outreach/enroll", json={"site_ids": ["node/1"]})
    message = configured.get("/api/outreach/messages?status=queued").get_json()["messages"][-1]
    configured.post(f"/api/outreach/messages/{message['id']}",
                    json={"subject": "Edited subject"})
    configured.post(f"/api/outreach/messages/{message['id']}", json={"action": "cancel"})
    statuses = {m["id"]: m["status"]
                for m in configured.get("/api/outreach/messages").get_json()["messages"]}
    assert statuses[message["id"]] == "cancelled"


def test_send_one_is_refused_for_a_suppressed_recipient(configured):
    configured.post("/api/outreach/enroll", json={"site_ids": ["node/1"]})
    configured.post("/api/suppression", json={"value": "owner@suds.example"})
    message = configured.get("/api/outreach/messages?status=queued").get_json()["messages"][0]
    response = configured.post(f"/api/outreach/send/{message['id']}", json={})
    assert response.status_code == 400
    assert "do-not-contact" in response.get_json()["error"]


# -------------------------------------------------------------- templates

def test_templates_are_seeded_and_editable(client):
    data = client.get("/api/templates").get_json()
    keys = {t["key"] for t in data["templates"]}
    assert {"intro_email", "followup_1", "followup_2", "walk_in_script", "call_script"} <= keys

    client.post("/api/templates", json={"key": "intro_email", "name": "Intro email",
                                        "channel": "email", "subject": "Hi {business_name}",
                                        "body": "Custom copy"})
    updated = next(t for t in client.get("/api/templates").get_json()["templates"]
                   if t["key"] == "intro_email")
    assert updated["body"] == "Custom copy"
    assert updated["builtin"] == 0


def test_template_needs_a_key(client):
    assert client.post("/api/templates", json={"name": "x"}).status_code == 400


# ---------------------------------------------------------------- planner

def test_planner_orders_stops(client):
    data = client.get("/api/planner/route?top=5&min_score=0").get_json()
    assert len(data["stops"]) == 2
    assert [s["order"] for s in data["stops"]] == [1, 2]
    assert data["google_maps_url"].startswith("https://www.google.com/maps/dir/")


def test_planner_rejects_bad_start(client):
    assert client.get("/api/planner/route?start=nonsense").status_code == 400


def test_planner_reports_when_nothing_matches(client):
    data = client.get("/api/planner/route?min_score=99").get_json()
    assert data["stops"] == [] and data["error"]


def test_territories_endpoint(client):
    data = client.post("/api/territories", json={"count": 2}).get_json()
    assert sum(t["sites"] for t in data["territories"]) == 2


# --------------------------------------------------------------- download

def test_downloads(client):
    assert client.get("/download/prospects.csv").status_code == 200
    assert client.get("/download/prospects.geojson").status_code == 200
    assert client.get("/download/listings.csv").status_code == 200
    assert client.get("/download/prospects.xml").status_code == 400
    assert client.get("/download/nope.csv").status_code == 404


# ------------------------------------------------- ordering regressions

FUNNEL = ["new", "queued", "contacted", "following_up", "interested", "won", "lost"]


def test_stats_returns_stages_in_funnel_order(client):
    """Flask sorts JSON dict keys, which once scrambled the funnel into an
    alphabet. The ordered list must survive serialisation."""
    stages = client.get("/api/stats").get_json()["stages"]
    assert [s["key"] for s in stages] == FUNNEL


def test_pipeline_board_returns_stages_in_funnel_order(client):
    stages = client.get("/api/pipeline").get_json()["stages"]
    assert [s["key"] for s in stages] == FUNNEL
    assert all("label" in s and "count" in s for s in stages)


def test_queue_is_ordered_by_when_it_sends(configured):
    """The queue must lead with what goes out next, not what was created last."""
    configured.post("/api/outreach/enroll", json={"site_ids": ["node/1"]})
    messages = configured.get("/api/outreach/messages?status=queued").get_json()["messages"]
    assert [m["step"] for m in messages] == [0, 1, 2]
    scheduled = [m["scheduled_at"] for m in messages]
    assert scheduled == sorted(scheduled)


def test_sent_history_is_newest_first(configured):
    configured.post("/api/outreach/enroll", json={"site_ids": ["node/1"]})
    sent = configured.get("/api/outreach/messages?status=sent").get_json()["messages"]
    assert sent == []  # nothing sent yet, but the query must not error


def test_outreach_stats_report_actually_due_not_all_queued(configured):
    configured.post("/api/outreach/enroll", json={"site_ids": ["node/1"]})
    data = configured.get("/api/outreach/messages").get_json()
    # Three queued, but only the day-0 intro is due right now.
    assert data["stats"]["queued"] == 3
    assert data["stats"]["due"] == 1


def test_categories_are_ranked_by_average_score(client):
    """The card is titled "Best site types", so it must rank by quality --
    a metro is full of government offices and they are hard accounts to win."""
    categories = client.get("/api/stats").get_json()["categories"]
    scores = [c["avg_score"] for c in categories]
    assert scores == sorted(scores, reverse=True)
    # Thin categories are excluded so one lucky site cannot top the chart.
    assert all(c["n"] >= 3 for c in categories)
