"""Personal budget app: US Bank CSV imports, merchant dictionary, cash flow and net worth."""
import os
from pathlib import Path

from flask import Flask

from . import db, personal

ROOT = Path(__file__).resolve().parent.parent


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

    conn = db.connect(app.config["DATABASE"])
    try:
        db.init_db(conn, personal.load(app.config["PERSONAL_CONFIG"]))
    finally:
        conn.close()

    app.teardown_appcontext(db.close_db)
    from .views import bp

    app.register_blueprint(bp)
    return app
