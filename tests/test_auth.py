import pytest

from machine_locator.db import Database
from machine_locator.web import auth
from machine_locator.web.app import create_app

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(auth.PASSWORD_ENV_VAR, raising=False)
    monkeypatch.delenv(auth.SECRET_ENV_VAR, raising=False)


def build(settings, public=False):
    app = create_app(settings, public=public)
    app.config["TESTING"] = True
    return app


# --------------------------------------------------------- password store

def test_hash_roundtrip():
    stored = auth.hash_password(PASSWORD)
    assert auth.verify_hash(stored, PASSWORD)
    assert not auth.verify_hash(stored, "something else")


def test_hash_is_salted():
    assert auth.hash_password(PASSWORD) != auth.hash_password(PASSWORD)


def test_plaintext_never_stored(settings):
    with Database(settings.db_path) as database:
        auth.set_password(database, PASSWORD)
        assert PASSWORD not in database.get_setting(auth.HASH_SETTING, "")


def test_corrupt_hash_rejects_rather_than_raising():
    assert auth.verify_hash("garbage", PASSWORD) is False
    assert auth.verify_hash("", PASSWORD) is False


def test_password_rules():
    assert auth.password_problem("short") 
    assert auth.password_problem("longenough", "different")
    assert auth.password_problem("longenough", "longenough") == ""


def test_env_password_wins_over_stored(settings, monkeypatch):
    with Database(settings.db_path) as database:
        auth.set_password(database, "stored-one-here")
        monkeypatch.setenv(auth.PASSWORD_ENV_VAR, "injected-one-here")
        assert auth.check_password(database, "injected-one-here")
        assert not auth.check_password(database, "stored-one-here")
        assert auth.password_is_from_env()


# ------------------------------------------------------- local, no password

def test_private_instance_needs_no_login(settings):
    client = build(settings).test_client()
    assert client.get("/").status_code == 200
    assert client.get("/api/stats").status_code == 200


def test_turning_the_login_on_from_settings(settings):
    client = build(settings).test_client()
    assert client.post("/api/password", json={
        "password": PASSWORD, "confirm": PASSWORD}).get_json()["protected"] is True

    fresh = build(settings).test_client()
    assert fresh.get("/").status_code == 302
    assert fresh.get("/api/stats").status_code == 401


def test_a_weak_password_is_refused(settings):
    client = build(settings).test_client()
    response = client.post("/api/password", json={"password": "abc", "confirm": "abc"})
    assert response.status_code == 400
    assert "8 characters" in response.get_json()["error"]


def test_mismatched_confirmation_is_refused(settings):
    client = build(settings).test_client()
    response = client.post("/api/password", json={"password": "longenough1",
                                                  "confirm": "longenough2"})
    assert "don't match" in response.get_json()["error"]


def test_changing_it_needs_the_current_one(settings):
    client = build(settings).test_client()
    client.post("/api/password", json={"password": PASSWORD, "confirm": PASSWORD})
    refused = client.post("/api/password", json={
        "current": "wrong", "password": "brand new one", "confirm": "brand new one"})
    assert refused.status_code == 400
    accepted = client.post("/api/password", json={
        "current": PASSWORD, "password": "brand new one", "confirm": "brand new one"})
    assert accepted.get_json()["ok"] is True


def test_it_can_be_turned_off_locally(settings):
    client = build(settings).test_client()
    client.post("/api/password", json={"password": PASSWORD, "confirm": PASSWORD})
    client.post("/api/password", json={"action": "remove", "current": PASSWORD})
    assert build(settings).test_client().get("/").status_code == 200


def test_an_env_password_cannot_be_changed_from_the_browser(settings, monkeypatch):
    monkeypatch.setenv(auth.PASSWORD_ENV_VAR, "injected-one-here")
    client = build(settings).test_client()
    client.post("/login", data={"password": "injected-one-here"})
    response = client.post(
        "/api/password", json={"current": "injected-one-here",
                               "password": "new one here", "confirm": "new one here"})
    assert response.status_code == 400
    assert "MACHINE_LOCATOR_PASSWORD" in response.get_json()["error"]


# ------------------------------------------------------------ signing in

@pytest.fixture
def guarded(settings):
    with Database(settings.db_path) as database:
        auth.set_password(database, PASSWORD)
    return build(settings)


def test_login_and_out(guarded):
    client = guarded.test_client()
    assert client.post("/login", data={"password": PASSWORD}).status_code == 302
    assert client.get("/api/stats").status_code == 200
    client.get("/logout")
    assert client.get("/api/stats").status_code == 401


def test_wrong_password_refused(guarded):
    client = guarded.test_client()
    body = client.post("/login", data={"password": "nope"}).get_data(as_text=True)
    assert "That password" in body and "banner-bad" in body
    assert client.get("/api/stats").status_code == 401


def test_api_gets_401_not_a_redirect(guarded):
    assert guarded.test_client().get("/api/stats").get_json()["error"] == "Not signed in."


def test_static_stays_reachable(guarded):
    assert guarded.test_client().get("/static/css/app.css").status_code == 200


def test_throttled_after_repeated_failures(guarded):
    client = guarded.test_client()
    for _ in range(auth.MAX_ATTEMPTS):
        client.post("/login", data={"password": "wrong"})
    assert "Too many attempts" in client.post(
        "/login", data={"password": "wrong"}).get_data(as_text=True)


def test_next_cannot_become_an_open_redirect(guarded):
    response = guarded.test_client().post("/login?next=https://evil.example/x",
                                          data={"password": PASSWORD})
    assert "evil.example" not in response.headers["Location"]


def test_next_honours_a_local_path(guarded):
    response = guarded.test_client().post("/login?next=/pipeline",
                                          data={"password": PASSWORD})
    assert response.headers["Location"].endswith("/pipeline")


# ----------------------------------------------------------- public first run

def test_public_instance_forces_setup_before_anything(settings):
    client = build(settings, public=True).test_client()
    response = client.get("/")
    assert response.status_code == 302 and "/setup" in response.headers["Location"]
    assert client.get("/api/stats").status_code == 401


def test_setup_page_renders(settings):
    body = build(settings, public=True).test_client().get("/setup").get_data(as_text=True)
    assert "Pick a password" in body


def test_setup_sets_the_password_and_signs_you_in(settings):
    client = build(settings, public=True).test_client()
    response = client.post("/setup", data={"password": PASSWORD, "confirm": PASSWORD})
    assert response.status_code == 302
    assert client.get("/api/stats").status_code == 200


def test_setup_rejects_a_weak_password(settings):
    client = build(settings, public=True).test_client()
    body = client.post("/setup", data={"password": "abc", "confirm": "abc"}).get_data(as_text=True)
    assert "at least 8" in body.lower()
    assert client.get("/api/stats").status_code == 401


def test_setup_is_closed_once_a_password_exists(settings):
    with Database(settings.db_path) as database:
        auth.set_password(database, PASSWORD)
    response = build(settings, public=True).test_client().get("/setup")
    assert response.status_code == 302 and "/login" in response.headers["Location"]


def test_a_public_instance_cannot_remove_its_password(settings):
    app = build(settings, public=True)
    client = app.test_client()
    client.post("/setup", data={"password": PASSWORD, "confirm": PASSWORD})
    response = client.post("/api/password", json={"action": "remove", "current": PASSWORD})
    assert response.status_code == 400
    assert "reachable from the internet" in response.get_json()["error"]


# ------------------------------------------------------------ secret key

def test_secret_key_is_stable(settings):
    with Database(settings.db_path) as database:
        assert auth.secret_key(database) == auth.secret_key(database)


def test_settings_api_never_echoes_a_secret(settings):
    client = build(settings).test_client()
    client.post("/api/settings", json={"smtp_password": "s3cret"})
    stored = client.get("/api/settings").get_json()
    assert "smtp_password" not in stored and "session_secret" not in stored
    assert auth.HASH_SETTING not in stored


@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1", True), ("localhost", True), ("::1", True),
    ("0.0.0.0", False), ("192.168.1.5", False),
])
def test_loopback_detection(host, expected):
    assert auth.is_loopback(host) is expected
