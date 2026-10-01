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


def txn(conn, account_id, day, amount, category_name=None, name="Made Up", **extra):
    """One hand-made transaction with a known category, its effective date synced. Returns its id."""
    from uuid import uuid4

    from budget.rules import sync_dates

    cid = category(conn, category_name) if category_name else None
    fields = {"account_id": account_id, "date": day, "effective_date": day, "amount": amount, "raw_description": name,
              "name": name, "category_id": cid, "category_source": "manual" if cid else "none",
              "dedupe_key": f"test|{uuid4().hex}", **extra}
    tid = conn.execute(
        f"INSERT INTO transactions ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})", tuple(fields.values())
    ).lastrowid
    sync_dates(conn, "id = ?", (tid,))
    return tid
