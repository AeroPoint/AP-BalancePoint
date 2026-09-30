"""Personal budget app: US Bank CSV imports, merchant dictionary, cash flow and net worth."""
import ipaddress
import os
from pathlib import Path

from flask import Flask, abort, request

from . import db, personal

ROOT = Path(__file__).resolve().parent.parent


TRUSTED_NETWORKS = [ipaddress.ip_network(n) for n in ("127.0.0.0/8", "::1/128", "100.64.0.0/10", "fd7a:115c:a1e0::/48")]


def trusted(address):
    """This computer, or a device on your Tailscale network (it hands out 100.64.0.0/10 addresses)."""
    try:
        ip = ipaddress.ip_address((address or "").split("%")[0])
    except ValueError:
        return False
    return any(ip in net for net in TRUSTED_NETWORKS if net.version == ip.version)


def create_app():
    data_dir = Path(os.environ.get("BUDGET_DATA_DIR", ROOT / "data"))
    for sub in ("", "uploads", "source"):
        (data_dir / sub).mkdir(parents=True, exist_ok=True)

    app = Flask(__name__)
    app.config.update(
        DATABASE=str(data_dir / "budget.db"),
        UPLOAD_DIR=str(data_dir / "uploads"),
        SOURCE_DIR=str(data_dir / "source"),
        PERSONAL_CONFIG=str(data_dir / "personal.toml"),
        SECRET_KEY="local-only-budget-app",
        MAX_CONTENT_LENGTH=50 * 1024 * 1024,
    )

    settings = personal.load(app.config["PERSONAL_CONFIG"])
    app.config["FEATURES"] = settings.features
    conn = db.connect(app.config["DATABASE"])
    try:
        db.init_db(conn, settings)
    finally:
        conn.close()

    app.teardown_appcontext(db.close_db)

    @app.before_request
    def only_trusted_devices():
        # The app has no login, so it only answers this computer and devices on your Tailscale
        # network (run.py serve --phones); anyone else on the same Wi-Fi gets turned away.
        if not trusted(request.remote_addr):
            abort(403)

    from .views import bp

    app.register_blueprint(bp)
    return app
