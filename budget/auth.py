"""Optional password, and extra trusted networks. Both are off unless their environment variable is set.

    BUDGET_PASSWORD=...          a password to type on a login page
    BUDGET_PASSWORD_HASH=...     the same, stored as a hash (python run.py hash-password prints one)
    BUDGET_SECRET_KEY=...        signs the login cookie; without it one is made and kept in the data folder
    BUDGET_TRUSTED_NETWORKS=...  comma-separated networks to answer besides this computer and Tailscale,
                                 e.g. 172.16.0.0/12 for Docker's own networks
"""
import hmac
import ipaddress
import os
import secrets
import time
from datetime import timedelta
from pathlib import Path

from flask import Blueprint, current_app, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

bp = Blueprint("auth", __name__)

# Requests that never need the password: the login page itself and the files it uses.
OPEN_ENDPOINTS = {"auth.login", "auth.logout", "static"}


def extra_networks(value):
    """BUDGET_TRUSTED_NETWORKS as a list of networks. A typo stops the app rather than trusting the wrong thing."""
    nets = []
    for part in (value or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            raise SystemExit(f"BUDGET_TRUSTED_NETWORKS: {part!r} isn't a network like 172.17.0.0/16")
    return nets


def password_settings():
    """(plain password, password hash), either or both None when the login is off."""
    return os.environ.get("BUDGET_PASSWORD") or None, os.environ.get("BUDGET_PASSWORD_HASH") or None


def secret_key(data_dir):
    """BUDGET_SECRET_KEY, else a random key kept in data/secret-key (only your user can read it)."""
    key = os.environ.get("BUDGET_SECRET_KEY")
    if key:
        return key
    path = Path(data_dir) / "secret-key"
    if not path.exists():
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass  # another process made it first
        else:
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_hex(32))
    return path.read_text().strip()


def check_password(given):
    plain, hashed = current_app.config["LOGIN_PASSWORD"], current_app.config["LOGIN_PASSWORD_HASH"]
    given = given or ""
    if hashed and check_password_hash(hashed, given):
        return True
    return bool(plain) and hmac.compare_digest(plain.encode(), given.encode())


def setup(app, data_dir):
    """Turn the login on when a password is set. With none set this changes nothing about the app."""
    plain, hashed = password_settings()
    if not (plain or hashed):
        app.config["LOGIN_REQUIRED"] = False
        return
    app.config.update(
        LOGIN_REQUIRED=True,
        LOGIN_PASSWORD=plain,
        LOGIN_PASSWORD_HASH=hashed,
        SECRET_KEY=secret_key(data_dir),
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    )
    app.register_blueprint(bp)

    @app.before_request
    def require_login():
        if session.get("signed_in") or request.endpoint in OPEN_ENDPOINTS:
            return None
        if request.method in ("GET", "HEAD"):
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return "Log in first.", 401


def _safe_next(target):
    # Only paths on this site, so the login page can't send anyone elsewhere.
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return url_for("main.dashboard")


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if check_password(request.form.get("password")):
            session.clear()
            session.permanent = True
            session["signed_in"] = True
            return redirect(_safe_next(request.form.get("next")))
        time.sleep(current_app.config.get("LOGIN_FAIL_DELAY", 1.0))  # slows down guessing
        error = "Wrong password."
    return render_template("login.html", error=error, next=request.values.get("next", "")), (401 if error else 200)


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
