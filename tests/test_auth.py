import pytest

from machine_locator.db import Database
from machine_locator.web import auth
from machine_locator.web.app import create_app


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(auth.PASSWORD_ENV_VAR, raising=False)
    monkeypatch.delenv(auth.SECRET_ENV_VAR, raising=False)


def build(settings):
    app = create_app(settings)
    app.config["TESTING"] = True
    return app


# ------------------------------------------------------- the bind guard

@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_needs_no_password(host):
    auth.check_bind_is_safe(host)  # must not raise


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "::"])
def test_public_bind_without_a_password_is_refused(host):
    with pytest.raises(auth.PublicBindWithoutPassword) as exc:
        auth.check_bind_is_safe(host)
    message = str(exc.value)
    assert auth.PASSWORD_ENV_VAR in message
    assert "send mail from your account" in message


def test_public_bind_is_allowed_once_a_password_exists(monkeypatch):
    monkeypatch.setenv(auth.PASSWORD_ENV_VAR, "hunter2-but-longer")
    auth.check_bind_is_safe("0.0.0.0")


# ------------------------------------------------------------ local mode

def test_no_password_means_no_login_wall(settings):
    client = build(settings).test_client()
    assert client.get("/").status_code == 200
    assert client.get("/api/stats").status_code == 200


# ----------------------------------------------------------- guarded mode

@pytest.fixture
def guarded(settings, monkeypatch):
    monkeypatch.setenv(auth.PASSWORD_ENV_VAR, "correct horse battery staple")
    return build(settings)


def test_pages_redirect_to_login(guarded):
    response = guarded.test_client().get("/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_api_returns_401_rather_than_a_redirect(guarded):
    """A fetch() should get a clean 401, not an HTML login page."""
    response = guarded.test_client().get("/api/stats")
    assert response.status_code == 401
    assert response.get_json()["error"] == "Not signed in."


def test_static_files_stay_reachable(guarded):
    """The login page needs its own stylesheet to render."""
    assert guarded.test_client().get("/static/css/app.css").status_code == 200


def test_login_page_renders(guarded):
    body = guarded.test_client().get("/login").get_data(as_text=True)
    assert "password protected" in body.lower()


def test_correct_password_signs_in(guarded):
    client = guarded.test_client()
    response = client.post("/login", data={"password": "correct horse battery staple"})
    assert response.status_code == 302
    assert client.get("/").status_code == 200
    assert client.get("/api/stats").status_code == 200


def test_wrong_password_does_not(guarded):
    client = guarded.test_client()
    body = client.post("/login", data={"password": "nope"}).get_data(as_text=True)
    # The apostrophe is HTML-escaped, so match on a stable fragment.
    assert "That password" in body and "banner-bad" in body
    assert client.get("/api/stats").status_code == 401


def test_sign_out_clears_the_session(guarded):
    client = guarded.test_client()
    client.post("/login", data={"password": "correct horse battery staple"})
    client.get("/logout")
    assert client.get("/api/stats").status_code == 401


def test_repeated_failures_are_throttled(guarded):
    client = guarded.test_client()
    for _ in range(auth.MAX_ATTEMPTS):
        client.post("/login", data={"password": "wrong"})
    body = client.post("/login", data={"password": "wrong"}).get_data(as_text=True)
    assert "Too many attempts" in body
    # Even the right password is refused while the lockout holds.
    client.post("/login", data={"password": "correct horse battery staple"})
    assert client.get("/api/stats").status_code == 401


def test_login_redirect_stays_on_this_site(guarded):
    """A next= parameter must not be usable as an open redirect."""
    client = guarded.test_client()
    response = client.post("/login?next=https://evil.example/steal",
                           data={"password": "correct horse battery staple"})
    assert "evil.example" not in response.headers["Location"]


def test_login_honours_a_local_next_path(guarded):
    client = guarded.test_client()
    response = client.post("/login?next=/pipeline",
                           data={"password": "correct horse battery staple"})
    assert response.headers["Location"].endswith("/pipeline")


# ------------------------------------------------------------ secret key

def test_secret_key_is_stable_across_restarts(settings):
    with Database(settings.db_path) as database:
        first = auth.secret_key(database)
        second = auth.secret_key(database)
    assert first == second and len(first) > 20


def test_environment_secret_wins(settings, monkeypatch):
    monkeypatch.setenv(auth.SECRET_ENV_VAR, "from-the-environment")
    with Database(settings.db_path) as database:
        assert auth.secret_key(database) == "from-the-environment"


def test_settings_api_never_echoes_a_secret(settings):
    client = build(settings).test_client()
    client.post("/api/settings", json={"smtp_password": "s3cret", "imap_password": "s3cret2"})
    stored = client.get("/api/settings").get_json()
    assert "smtp_password" not in stored
    assert "imap_password" not in stored
    assert "session_secret" not in stored
