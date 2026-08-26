import pytest
import requests

from machine_locator.routes.http import PoliteClient, RobotsDisallowed

UA = "machine-locator/0.1 (test)"


class FakeResponse:
    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code


def install_fake_session(monkeypatch, robots_text, page_status=200, page_text="<html></html>"):
    """Serve a canned robots.txt and a canned page for everything else."""
    calls = []

    def fake_get(self, url, timeout=None):
        calls.append(url)
        if url.endswith("/robots.txt"):
            if robots_text is None:
                return FakeResponse("", 404)
            return FakeResponse(robots_text, 200)
        return FakeResponse(page_text, page_status)

    monkeypatch.setattr(requests.Session, "get", fake_get)
    return calls


def make_client(**kwargs):
    return PoliteClient(user_agent=UA, rate_limit_seconds=0.0, **kwargs)


def test_disallowed_path_is_refused(monkeypatch):
    install_fake_session(monkeypatch, "User-agent: *\nDisallow: /search\n")
    client = make_client()
    assert client.allowed("https://site.example/search?q=vending") is False
    with pytest.raises(RobotsDisallowed, match="robots.txt"):
        client.get("https://site.example/search?q=vending")


def test_allowed_path_is_fetched(monkeypatch):
    install_fake_session(monkeypatch, "User-agent: *\nDisallow: /admin\n", page_text="hello")
    client = make_client()
    assert client.allowed("https://site.example/listings") is True
    assert client.get("https://site.example/listings").text == "hello"


def test_missing_robots_txt_allows_everything(monkeypatch):
    install_fake_session(monkeypatch, None)
    assert make_client().allowed("https://site.example/anything") is True


def test_unreachable_robots_txt_is_treated_as_disallow(monkeypatch):
    def fake_get(self, url, timeout=None):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(requests.Session, "get", fake_get)
    assert make_client().allowed("https://site.example/x") is False


def test_ignore_robots_overrides_the_check(monkeypatch):
    install_fake_session(monkeypatch, "User-agent: *\nDisallow: /\n", page_text="page")
    client = make_client(respect_robots=False)
    assert client.allowed("https://site.example/search") is True
    assert client.get("https://site.example/search").text == "page"


def test_robots_txt_is_fetched_once_per_host(monkeypatch):
    calls = install_fake_session(monkeypatch, "User-agent: *\nDisallow: /admin\n")
    client = make_client()
    for _ in range(3):
        client.get("https://site.example/listings")
    assert calls.count("https://site.example/robots.txt") == 1


def test_403_is_reported_with_a_workaround(monkeypatch):
    install_fake_session(monkeypatch, None, page_status=403)
    with pytest.raises(RobotsDisallowed, match="routes import"):
        make_client().get("https://site.example/search")


def test_server_errors_are_retried_then_surfaced(monkeypatch):
    install_fake_session(monkeypatch, None, page_status=503)
    monkeypatch.setattr("time.sleep", lambda _s: None)
    with pytest.raises(RuntimeError, match="HTTP 503"):
        make_client().get("https://site.example/search", retries=1)


def test_throttle_records_last_request_per_host(monkeypatch):
    install_fake_session(monkeypatch, None)
    client = make_client()
    client.get("https://a.example/x")
    client.get("https://b.example/x")
    assert set(client._last_request) == {"a.example", "b.example"}


def test_robots_reason_distinguishes_unreachable_from_disallowed(monkeypatch):
    """A network failure must not be reported as the site refusing us."""
    def unreachable(self, url, timeout=None):
        raise requests.ConnectionError("proxy refused CONNECT")

    monkeypatch.setattr(requests.Session, "get", unreachable)
    client = make_client()
    assert client.robots_reason("https://site.example/x") == "robots.txt unreachable"
    with pytest.raises(RobotsDisallowed, match="not the site refusing you"):
        client.get("https://site.example/x")


def test_robots_reason_for_a_real_disallow(monkeypatch):
    install_fake_session(monkeypatch, "User-agent: *\nDisallow: /search\n")
    client = make_client()
    assert client.robots_reason("https://site.example/search") == "disallowed by robots.txt"
    assert client.robots_reason("https://site.example/ok") == "allowed"


def test_robots_reason_when_no_file_exists(monkeypatch):
    install_fake_session(monkeypatch, None)
    assert make_client().robots_reason("https://site.example/x") == "no robots.txt"


def test_robots_reason_when_overridden(monkeypatch):
    install_fake_session(monkeypatch, "User-agent: *\nDisallow: /\n")
    client = make_client(respect_robots=False)
    assert client.robots_reason("https://site.example/x") == "ignored (--ignore-robots)"
