"""A deliberately well-behaved HTTP client for the listing scrapers.

Business-for-sale sites are small operations with real bandwidth bills, and
several of them disallow crawling in robots.txt. This client therefore:

* reads and honours robots.txt by default (``--ignore-robots`` exists, but it
  is opt-in and the CLI tells you what you are overriding);
* rate limits per domain rather than globally, so one slow host does not stall
  the others;
* identifies itself honestly in the User-Agent;
* retries only on transient failures, never on a 403.
"""

from __future__ import annotations

import time
import urllib.robotparser as robotparser
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import urljoin, urlparse

import requests


class RobotsDisallowed(RuntimeError):
    """Raised when robots.txt forbids the URL and we are respecting it."""


@dataclass
class FetchResult:
    url: str
    status: int
    text: str
    from_cache: bool = False


class PoliteClient:
    def __init__(
        self,
        user_agent: str,
        rate_limit_seconds: float = 2.0,
        timeout: int = 45,
        respect_robots: bool = True,
    ):
        self.user_agent = user_agent
        self.rate_limit_seconds = rate_limit_seconds
        self.timeout = timeout
        self.respect_robots = respect_robots
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        self._last_request: Dict[str, float] = {}
        self._robots: Dict[str, Optional[robotparser.RobotFileParser]] = {}
        # Why each origin ended up allowed or refused. "unreachable" is not the
        # same as "disallowed", and telling a user the wrong one sends them
        # debugging the wrong problem.
        self._robots_status: Dict[str, str] = {}

    # ---------------------------------------------------------------- robots

    def _robots_for(self, url: str) -> Optional[robotparser.RobotFileParser]:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._robots:
            return self._robots[origin]

        parser = robotparser.RobotFileParser()
        robots_url = urljoin(origin, "/robots.txt")
        try:
            response = self.session.get(robots_url, timeout=self.timeout)
            if response.status_code >= 400:
                # No robots.txt at all means nothing is disallowed.
                parser = None  # type: ignore[assignment]
                self._robots_status[origin] = "missing"
            else:
                parser.parse(response.text.splitlines())
                self._robots_status[origin] = "fetched"
        except requests.RequestException:
            # Unreachable robots.txt: err on the side of not crawling, but
            # remember that we never actually read a rule.
            parser.parse(["User-agent: *", "Disallow: /"])
            self._robots_status[origin] = "unreachable"
        self._robots[origin] = parser
        return parser

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parser = self._robots_for(url)
        if parser is None:
            return True
        return parser.can_fetch(self.user_agent, url) or parser.can_fetch("*", url)

    def robots_reason(self, url: str) -> str:
        """A human explanation of the robots decision for this URL."""
        if not self.respect_robots:
            return "ignored (--ignore-robots)"
        allowed = self.allowed(url)
        origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        status = self._robots_status.get(origin, "fetched")
        if status == "unreachable":
            return "robots.txt unreachable"
        if allowed:
            return "allowed" if status == "fetched" else "no robots.txt"
        return "disallowed by robots.txt"

    # ----------------------------------------------------------------- fetch

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        last = self._last_request.get(host)
        if last is not None:
            wait = self.rate_limit_seconds - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_request[host] = time.monotonic()

    def get(self, url: str, retries: int = 2) -> FetchResult:
        if not self.allowed(url):
            host = urlparse(url).netloc
            if self.robots_reason(url) == "robots.txt unreachable":
                raise RobotsDisallowed(
                    f"could not reach https://{host}/robots.txt, so {host} was not "
                    "crawled. Check your network or proxy -- this is not the site "
                    "refusing you."
                )
            raise RobotsDisallowed(f"robots.txt on {host} disallows {url}")

        last_error: Optional[Exception] = None
        for attempt in range(retries + 1):
            self._throttle(url)
            try:
                response = self.session.get(url, timeout=self.timeout)
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(2 ** attempt)
                continue

            if response.status_code in (403, 401):
                # A hard "no". Retrying just annoys the server and gets the IP
                # banned; surface it so the CLI can tell the user why.
                raise RobotsDisallowed(
                    f"{urlparse(url).netloc} returned {response.status_code} -- "
                    "this site blocks automated access. Use its email alerts, or "
                    "export results and import them with `mloc routes import`."
                )
            if response.status_code >= 500 or response.status_code == 429:
                last_error = RuntimeError(f"HTTP {response.status_code}")
                time.sleep(2 ** attempt * 2)
                continue

            return FetchResult(url=url, status=response.status_code, text=response.text)

        raise RuntimeError(f"failed to fetch {url}: {last_error}")
