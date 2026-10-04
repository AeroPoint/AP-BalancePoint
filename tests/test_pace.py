"""The Overview's pace check: flexible spending against the month's pace, and the cushion."""
from datetime import date

from budget import budgeting

from conftest import account, txn


def setup(conn, spent_by_day10, balance):
    chk = account(conn, "Joint Checking")
    conn.execute("INSERT INTO balances (account_id, month, amount, as_of, source) VALUES (?, '2026-03', ?, '2026-03-01', 'manual')", (chk, balance))
    budgeting.set_setting(conn, "flex_target", 3100)
    budgeting.set_setting(conn, "plan_account", chk)
    budgeting.set_setting(conn, "plan_buffer", 2000)
    txn(conn, chk, "2026-03-05", -spent_by_day10, "Groceries")
    txn(conn, chk, "2026-03-25", -500, "Groceries")  # later in the month: not counted on day 10
    conn.commit()


def test_under_pace(conn):
    setup(conn, 600, 5000)
    p = budgeting.pace(conn, date(2026, 3, 10))
    assert (p["day"], p["days"], p["spent"], p["expected"]) == (10, 31, 600, 1000)
    assert p["ahead"] == -400 and p["warnings"] == []
    assert round(p["per_day_left"], 2) == round(2500 / 22, 2)


def test_ahead_of_pace_and_below_cushion_warn(conn, client):
    setup(conn, 1600, 2500)
    p = budgeting.pace(conn, date(2026, 3, 10))
    assert p["ahead"] == 600
    assert any("ahead of pace" in w for w in p["warnings"])
    assert any("below" in w and "cushion" in w for w in p["warnings"])  # 2500 - 1600 = 900 < 2000


def test_over_target(conn):
    setup(conn, 3300, 9000)
    assert any("over the month" in w for w in budgeting.pace(conn, date(2026, 3, 10))["warnings"])


def test_no_target_no_plan(conn):
    assert budgeting.pace(conn, date(2026, 3, 10)) is None
