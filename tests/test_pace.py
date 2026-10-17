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


def test_this_months_pending_charges_count(conn):
    """The last sync's snapshot of this month's pending charges: flexible ones count, others and old months don't."""
    import json
    setup(conn, 600, 5000)
    groceries = conn.execute("SELECT id FROM categories WHERE name = 'Groceries'").fetchone()[0]
    rent = conn.execute("SELECT id FROM categories WHERE grp = 'fixed' AND kind = 'expense' LIMIT 1").fetchone()[0]
    items = [{"account_id": 1, "amount": -80.0, "raw": "SAFEWAY", "category_id": groceries},
             {"account_id": 1, "amount": -20.0, "raw": "NEW PLACE", "category_id": None},
             {"account_id": 1, "amount": -900.0, "raw": "RENT", "category_id": rent}]
    budgeting.set_setting(conn, "pending_this_month", json.dumps({"month": "2026-03", "items": items}))
    p = budgeting.pace(conn, date(2026, 3, 10))
    assert (p["pending"], p["spent"]) == (100, 700)
    assert budgeting.pace(conn, date(2026, 4, 2))["pending"] == 0  # a snapshot from last month is ignored


def _snapshot(conn, items, month="2026-03"):
    import json
    budgeting.set_setting(conn, "pending_this_month", json.dumps({"month": month, "items": items}))
    conn.commit()


def test_pace_can_leave_pending_out(conn):
    setup(conn, 600, 5000)
    groceries = conn.execute("SELECT id FROM categories WHERE name = 'Groceries'").fetchone()[0]
    _snapshot(conn, [{"account_id": 1, "amount": -80.0, "raw": "SAFEWAY", "category_id": groceries}])
    assert budgeting.pace(conn, date(2026, 3, 10))["pending"] == 80
    budgeting.set_setting(conn, budgeting.PACE_COUNTS_PENDING, "0")
    p = budgeting.pace(conn, date(2026, 3, 10))
    assert (p["pending"], p["spent"]) == (0, 600)


def test_transactions_show_pending_marked_and_filtered(conn, client, monkeypatch):
    """This month's pending charges sit above the list, marked Pending, out of its totals; the setting hides them."""
    from datetime import date as real_date
    from budget import views
    today = real_date.today()
    chk, card = account(conn, "Checking"), account(conn, "Card", "credit")
    groceries = conn.execute("SELECT id FROM categories WHERE name = 'Groceries'").fetchone()[0]
    _snapshot(conn, [
        {"account_id": chk, "amount": -42.5, "raw": "CORNER MARKET 12", "name": "Corner Market", "date": today.isoformat(), "category_id": groceries},
        {"account_id": card, "amount": -9.99, "raw": "STREAMFLIX", "date": today.isoformat(), "category_id": None},
    ], month=today.isoformat()[:7])
    html = client.get("/transactions").get_data(as_text=True)
    assert "Corner Market" in html and "streamflix" in html.lower()
    assert html.count('class="is-pending"') == 2 and "not in the totals" in html
    assert client.get(f"/transactions?account={card}").get_data(as_text=True).count('class="is-pending"') == 1
    assert client.get("/transactions?category=none").get_data(as_text=True).count('class="is-pending"') == 1
    assert client.get("/transactions?q=corner").get_data(as_text=True).count('class="is-pending"') == 1
    assert 'class="is-pending"' not in client.get("/transactions?period=2020-01").get_data(as_text=True)
    assert 'class="is-pending"' not in client.get("/transactions?flag=check").get_data(as_text=True)
    client.post("/bank-sync/pending", data={"pace_counts_pending": "1"})  # show_pending left unticked
    assert 'class="is-pending"' not in client.get("/transactions").get_data(as_text=True)
    assert budgeting.setting_on(conn, budgeting.PACE_COUNTS_PENDING)
