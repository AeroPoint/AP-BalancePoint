"""The Overview's month forecast: spending so far plus what's known to be coming, against the targets."""
from datetime import date

from budget import budgeting

from conftest import account, category, txn


def setup(conn, spent_by_day10, balance):
    chk = account(conn, "Joint Checking")
    conn.execute("INSERT INTO balances (account_id, month, amount, as_of, source) VALUES (?, '2026-03', ?, '2026-03-01', 'manual')", (chk, balance))
    budgeting.set_setting(conn, "flex_target", 3100)
    budgeting.set_setting(conn, "plan_account", chk)
    budgeting.set_setting(conn, "plan_buffer", 2000)
    txn(conn, chk, "2026-03-05", -spent_by_day10, "Groceries")
    txn(conn, chk, "2026-03-25", -500, "Groceries")  # later in the month: not counted on day 10
    conn.commit()


def test_on_track(conn):
    setup(conn, 600, 5000)
    p = budgeting.forecast(conn, date(2026, 3, 10))
    assert (p["day"], p["days"], p["days_left"], p["spent"], p["forecast"]) == (10, 31, 22, 600, 600)
    assert p["warnings"] == []
    assert round(p["per_day_left"], 2) == round(2500 / 22, 2)


def test_short_of_the_plans_day_and_below_cushion_warn(conn):
    setup(conn, 1600, 2500)
    chk = account(conn, "Joint Checking")
    for month in ("2026-01", "2026-02"):
        txn(conn, chk, f"{month}-10", -3720, "Groceries")
    conn.commit()
    p = budgeting.forecast(conn, date(2026, 3, 10))
    assert (round(p["planned_per_day"]), round(p["per_day_left"]), round(p["usual_per_day"])) == (100, 68, 120)
    assert any("$68 a day is left for flexible spending through Mar 31, under the plan's $100 a day." == w for w in p["warnings"])
    assert any("below" in w and "cushion" in w for w in p["warnings"])  # 2500 - 1600 = 900 < 2000


def test_over_target(conn):
    setup(conn, 3300, 9000)
    assert any("over the $3,100 target" in w for w in budgeting.forecast(conn, date(2026, 3, 10))["warnings"])


def test_no_target_no_plan(conn):
    assert budgeting.forecast(conn, date(2026, 3, 10)) is None


def test_regular_bills_still_to_come_count(conn):
    """A monthly bill not charged yet this month is still coming; one already charged, or pending, isn't."""
    chk = account(conn, "Joint Checking")
    budgeting.set_setting(conn, "plan_fixed", 1000)
    budgeting.set_setting(conn, "flex_target", 3000)
    for month in ("2025-11", "2025-12", "2026-01", "2026-02"):
        txn(conn, chk, f"{month}-20", -100, "Bills & Utilities", name="Power Co")
        txn(conn, chk, f"{month}-05", -60, "Bills & Utilities", name="Phone Co")
        txn(conn, chk, f"{month}-07", -40, "Bills & Utilities", name="Insure Co")
        txn(conn, chk, f"{month}-12", -25, "Giving", name="Charity")
    txn(conn, chk, "2026-03-05", -60, "Bills & Utilities", name="Phone Co")  # already paid this month
    conn.commit()
    _snapshot(conn, [{"account_id": chk, "amount": -41.0, "raw": "INSURE CO", "name": "Insure Co",
                      "date": "2026-03-08", "category_id": category(conn, "Bills & Utilities")}])
    p = budgeting.forecast(conn, date(2026, 3, 10))
    coming = {(c["name"], c["grp"]) for c in p["coming"]}
    assert coming == {("Power Co", "fixed"), ("Charity", "flexible")}
    fixed = p["groups"]["fixed"]
    assert (fixed["spent"], fixed["pending"], fixed["upcoming"], fixed["forecast"]) == (101, 41, 100, 201)
    assert p["groups"]["flexible"]["upcoming"] == 25
    assert round(p["per_day_left"], 2) == round((3000 - 25) / 22, 2)


def test_expected_changes_count_until_charged(conn):
    """A plan change with a flexible or fixed category is still coming until a charge like it shows up; one
    on top of the targets is listed apart and moves the plan, while one inside a target doesn't."""
    setup(conn, 600, 5000)
    conn.executemany("INSERT INTO plan_items (label, amount, start_month, end_month, category_id) VALUES (?, ?, ?, ?, ?)", [
        ("Birthday party", -300, "2026-03", "2026-03", category(conn, "Dining & Drinks")),
        ("Camp deposit", -200, "2026-03", "2026-03", category(conn, "Kids & Education")),
        ("Property tax", -2000, "2026-03", "2026-03", None),
        ("Property tax", -2000, "2026-04", "2026-04", None),
        ("Gym fee", -50, "2026-04", "2026-04", category(conn, "Health & Fitness")),
    ])
    txn(conn, account(conn, "Joint Checking"), "2026-03-08", -190, "Kids & Education", name="Camp Co")  # the camp deposit
    conn.commit()
    p = budgeting.forecast(conn, date(2026, 3, 10))
    assert [(c["name"], c["amount"], c["grp"]) for c in p["coming"]] == [("Birthday party", 300, "flexible")]
    assert p["forecast"] == 600 + 190 + 300
    assert [(e["label"], e["made"]) for e in p["outside"]] == [("Property tax", False)]
    budgeting.set_setting(conn, "plan_income", 1000)
    rows = budgeting.project(conn, months=2, today=date(2026, 3, 10))["rows"]
    assert rows[0]["key"] == "2026-04" and rows[0]["adjust"] == -2000  # the gym fee is inside April's target


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
    p = budgeting.forecast(conn, date(2026, 3, 10))
    assert (p["pending"], p["spent"]) == (100, 700)
    assert budgeting.forecast(conn, date(2026, 4, 2))["pending"] == 0  # a snapshot from last month is ignored


def _snapshot(conn, items, month="2026-03"):
    import json
    budgeting.set_setting(conn, "pending_this_month", json.dumps({"month": month, "items": items}))
    conn.commit()


def test_pace_can_leave_pending_out(conn):
    setup(conn, 600, 5000)
    groceries = conn.execute("SELECT id FROM categories WHERE name = 'Groceries'").fetchone()[0]
    _snapshot(conn, [{"account_id": 1, "amount": -80.0, "raw": "SAFEWAY", "category_id": groceries}])
    assert budgeting.forecast(conn, date(2026, 3, 10))["pending"] == 80
    budgeting.set_setting(conn, budgeting.PACE_COUNTS_PENDING, "0")
    p = budgeting.forecast(conn, date(2026, 3, 10))
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
