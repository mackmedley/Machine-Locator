"""Password protection for a Machine Locator that is reachable from the internet.

Locally this is off: an app on 127.0.0.1 is already only reachable by whoever
is sitting at the machine, and a login screen there is friction with no benefit.

The moment the app binds to a public interface, the calculation inverts. The
database holds an SMTP password, a prospect list and an outreach queue -- an
unprotected public instance would let a stranger send mail from the operator's
own account. So:

* Setting ``MACHINE_LOCATOR_PASSWORD`` turns authentication on.
* Binding to anything other than loopback *without* that variable is refused
  outright, rather than started with a warning nobody reads.

One password, one operator. This is a tool for a person running a vending
route, not a multi-tenant SaaS, and pretending otherwise would add accounts,
roles and password resets that nobody asked for.
"""

from __future__ import annotations

import hmac
import os
import secrets
import time
from typing import Callable, Dict, Tuple

from flask import (
    Flask, redirect, render_template, request, session, url_for,
)

PASSWORD_ENV_VAR = "MACHINE_LOCATOR_PASSWORD"
SECRET_ENV_VAR = "MACHINE_LOCATOR_SECRET_KEY"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0.localhost"}

# Brute-force slowing. Kept in memory deliberately: a restart clearing it is
# fine, and a shared store would be infrastructure this app does not need.
MAX_ATTEMPTS = 6
LOCKOUT_SECONDS = 300


class PublicBindWithoutPassword(RuntimeError):
    """Raised when the app is asked to listen publicly with no password set."""


def configured_password() -> str:
    return os.environ.get(PASSWORD_ENV_VAR, "").strip()


def auth_enabled() -> bool:
    return bool(configured_password())


def is_loopback(host: str) -> bool:
    return (host or "").strip().lower() in LOOPBACK_HOSTS


def check_bind_is_safe(host: str) -> None:
    """Refuse to serve a public interface without a password.

    Called before the server starts, so the failure is a clear message at
    launch rather than a quietly exposed instance.
    """
    if is_loopback(host) or auth_enabled():
        return
    raise PublicBindWithoutPassword(
        f"Refusing to listen on {host} without a password.\n\n"
        "This app stores your email password, your prospect list and your "
        "outreach queue. On a public address, anyone who finds the URL could "
        "send mail from your account.\n\n"
        f"Set a password first:\n\n"
        f"    export {PASSWORD_ENV_VAR}='something-long-and-random'\n\n"
        "Then start it again. To run privately on this machine instead, use "
        "the default host (127.0.0.1)."
    )


def secret_key(db) -> str:
    """A stable signing key, so sessions survive a restart.

    Prefers the environment; otherwise generates one once and keeps it in the
    local database.
    """
    from_env = os.environ.get(SECRET_ENV_VAR, "").strip()
    if from_env:
        return from_env
    stored = db.get_setting("session_secret", "")
    if not stored:
        stored = secrets.token_urlsafe(48)
        db.set_setting("session_secret", stored)
    return stored


class LoginThrottle:
    """Slows repeated failures from one address to make guessing impractical."""

    def __init__(self) -> None:
        self._failures: Dict[str, Tuple[int, float]] = {}

    def locked_for(self, key: str) -> int:
        count, until = self._failures.get(key, (0, 0.0))
        if count >= MAX_ATTEMPTS and until > time.monotonic():
            return int(until - time.monotonic())
        return 0

    def record_failure(self, key: str) -> None:
        count, _ = self._failures.get(key, (0, 0.0))
        count += 1
        self._failures[key] = (count, time.monotonic() + LOCKOUT_SECONDS)

    def clear(self, key: str) -> None:
        self._failures.pop(key, None)


def install(app: Flask, db_factory: Callable[[], object]) -> None:
    """Wire authentication into an app, if a password is configured."""
    with db_factory() as database:  # type: ignore[attr-defined]
        app.secret_key = secret_key(database)

    # Session cookies should not travel over plain HTTP on a public host, and
    # should never be readable from JavaScript.
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(os.environ.get("MACHINE_LOCATOR_HTTPS")),
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
    )

    if not auth_enabled():
        app.config["AUTH_ENABLED"] = False
        return

    app.config["AUTH_ENABLED"] = True
    throttle = LoginThrottle()

    def client_key() -> str:
        # Behind a host's proxy the real address is in X-Forwarded-For.
        forwarded = request.headers.get("X-Forwarded-For", "")
        return (forwarded.split(",")[0].strip() or request.remote_addr or "?")

    @app.before_request
    def require_login():
        if request.endpoint in ("login", "static") or request.path.startswith("/static/"):
            return None
        if session.get("authenticated"):
            return None
        if request.path.startswith("/api/") or request.path.startswith("/download/"):
            return {"error": "Not signed in."}, 401
        return redirect(url_for("login", next=request.path))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = ""
        wait = throttle.locked_for(client_key())

        if request.method == "POST" and not wait:
            supplied = request.form.get("password", "")
            if hmac.compare_digest(supplied, configured_password()):
                session["authenticated"] = True
                session.permanent = True
                throttle.clear(client_key())
                target = request.args.get("next", "")
                # Only ever redirect within this app.
                if not target.startswith("/") or target.startswith("//"):
                    target = url_for("index")
                return redirect(target)
            throttle.record_failure(client_key())
            error = "That password isn't right."
            wait = throttle.locked_for(client_key())

        return render_template("login.html", error=error, wait=wait)

    @app.route("/logout", methods=["POST", "GET"])
    def logout():
        session.clear()
        return redirect(url_for("login"))
