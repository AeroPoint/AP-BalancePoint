"""Every test gets its own empty data folder; nothing here ever reads or writes the real data/."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from budget import create_app  # noqa: E402
from budget.db import connect  # noqa: E402


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("BUDGET_DATA_DIR", str(tmp_path / "data"))
    return create_app()


@pytest.fixture
def conn(app):
    c = connect(app.config["DATABASE"])
    yield c
    c.close()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def demo_conn(conn):
    from budget import demo

    demo.build(conn)
    return conn


def account(conn, name, kind="checking"):
    from budget.excel_import import ensure_account

    return ensure_account(conn, name, kind)


def category(conn, name):
    return conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()[0]
