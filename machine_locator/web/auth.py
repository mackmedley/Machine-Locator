"""A deliberately small login.

One password, one operator. This is a tool for a person running a vending
route, not a service with accounts, roles and password resets.

How it decides whether to ask for a password:

* **No password set** -> no login at all. On your own computer that is the
  right default; an app on 127.0.0.1 is already only reachable by whoever is
  sitting at it, and a login screen there is friction for nothing.
* **A password set** -> one screen, one field, stays signed in for a month.

You set it in the browser (Settings, or the first-run screen). There is also a
``MACHINE_LOCATOR_PASSWORD`` environment variable for hosts that prefer to
inject secrets; when it is set it wins and the in-app password is ignored.

On a public address a password is not optional. Rather than refusing to start,
a fresh public instance shows a "pick a password" screen and refuses to do
anything else until one is chosen -- which is the same first-run flow as most
self-hosted software, and far easier than setting an environment variable
before you can see the app at all.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Callable, Dict, Optional, Tuple

from flask import Flask, redirect, render_template, request, session, url_for

PASSWORD_ENV_VAR = "MACHINE_LOCATOR_PASSWORD"
SECRET_ENV_VAR = "MACHINE_LOCATOR_SECRET_KEY"
HASH_SETTING = "auth_password_hash"

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
MIN_PASSWORD_LENGTH = 8

# Brute-force slowing, kept in memory on purpose: losing it on restart is fine,
# and a shared store would be infrastructure this app does not need.
MAX_ATTEMPTS = 6
LOCKOUT_SECONDS = 300

PBKDF2_ROUNDS = 240_000


# ------------------------------------------------------------- password store

def hash_password(plaintext: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", plaintext.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2${PBKDF2_ROUNDS}${salt.hex()}${digest.hex()}"


def verify_hash(stored: str, plaintext: str) -> bool:
    try:
        scheme, rounds, salt_hex, digest_hex = stored.split("$")
        if scheme != "pbkdf2":
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", plaintext.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
        return hmac.compare_digest(candidate.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def env_password() -> str:
    return os.environ.get(PASSWORD_ENV_VAR, "").strip()


def has_password(db) -> bool:
    return bool(env_password() or db.get_setting(HASH_SETTING, ""))


def password_is_from_env() -> bool:
    """True when the host injects it, so the UI hides the change-password form."""
    return bool(env_password())


def set_password(db, plaintext: str) -> None:
    db.set_setting(HASH_SETTING, hash_password(plaintext))


def clear_password(db) -> None:
    db.set_setting(HASH_SETTING, "")


def check_password(db, plaintext: str) -> bool:
    injected = env_password()
    if injected:
        return hmac.compare_digest(plaintext, injected)
    stored = db.get_setting(HASH_SETTING, "")
    return bool(stored) and verify_hash(stored, plaintext)


def password_problem(plaintext: str, confirm: Optional[str] = None) -> str:
    """Why this password is not acceptable, or '' if it is."""
    if len(plaintext) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if confirm is not None and plaintext != confirm:
        return "The two passwords don't match."
    return ""


# ------------------------------------------------------------------- binding

def is_loopback(host: str) -> bool:
    return (host or "").strip().lower() in LOOPBACK_HOSTS


def secret_key(db) -> str:
    """A stable signing key, so sessions survive a restart."""
    from_env = os.environ.get(SECRET_ENV_VAR, "").strip()
    if from_env:
        return from_env
    stored = db.get_setting("session_secret", "")
    if not stored:
        stored = secrets.token_urlsafe(48)
        db.set_setting("session_secret", stored)
    return stored


class LoginThrottle:
    def __init__(self) -> None:
        self._failures: Dict[str, Tuple[int, float]] = {}

    def locked_for(self, key: str) -> int:
        count, until = self._failures.get(key, (0, 0.0))
        if count >= MAX_ATTEMPTS and until > time.monotonic():
            return int(until - time.monotonic())
        return 0

    def record_failure(self, key: str) -> None:
        count, _ = self._failures.get(key, (0, 0.0))
        self._failures[key] = (count + 1, time.monotonic() + LOCKOUT_SECONDS)

    def clear(self, key: str) -> None:
        self._failures.pop(key, None)


OPEN_ENDPOINTS = {"login", "setup", "static"}


def install(app: Flask, db_factory: Callable[[], object], public: bool = False) -> None:
    """Wire the login in. Whether it actually asks for a password is decided
    per request, because the password can be set from the UI while running."""
    with db_factory() as database:  # type: ignore[attr-defined]
        app.secret_key = secret_key(database)

    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(os.environ.get("MACHINE_LOCATOR_HTTPS")),
        PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
        # Reachable from outside this machine, so a password is mandatory.
        PUBLIC_INSTANCE=public,
    )
    throttle = LoginThrottle()

    def client_key() -> str:
        forwarded = request.headers.get("X-Forwarded-For", "")
        return forwarded.split(",")[0].strip() or request.remote_addr or "?"

    def wants_json() -> bool:
        return request.path.startswith("/api/") or request.path.startswith("/download/")

    @app.before_request
    def gate():
        if request.endpoint in OPEN_ENDPOINTS or request.path.startswith("/static/"):
            return None
        with db_factory() as database:  # type: ignore[attr-defined]
            protected = has_password(database)
        if not protected:
            # A public instance with no password yet: nothing works until one
            # is chosen, so the URL is never usable by a passer-by.
            if app.config["PUBLIC_INSTANCE"]:
                if wants_json():
                    return {"error": "Set a password first."}, 401
                return redirect(url_for("setup"))
            return None
        if session.get("authenticated"):
            return None
        if wants_json():
            return {"error": "Not signed in."}, 401
        return redirect(url_for("login", next=request.path))

    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        with db_factory() as database:  # type: ignore[attr-defined]
            if has_password(database):
                return redirect(url_for("login"))
            error = ""
            if request.method == "POST":
                chosen = request.form.get("password", "")
                error = password_problem(chosen, request.form.get("confirm"))
                if not error:
                    set_password(database, chosen)
                    session["authenticated"] = True
                    session.permanent = True
                    return redirect(url_for("index"))
            return render_template("setup.html", error=error,
                                   min_length=MIN_PASSWORD_LENGTH)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        with db_factory() as database:  # type: ignore[attr-defined]
            if not has_password(database):
                if app.config["PUBLIC_INSTANCE"]:
                    return redirect(url_for("setup"))
                return redirect(url_for("index"))

            error = ""
            wait = throttle.locked_for(client_key())
            if request.method == "POST" and not wait:
                if check_password(database, request.form.get("password", "")):
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
