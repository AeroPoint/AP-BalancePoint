"""Recurring page: regular charges found in spending, their flags, and "Not recurring"."""
from datetime import date, timedelta

from budget import recurring
from budget.reports import add_months

from conftest import account, txn

TODAY = date(2026, 6, 20)


def monthly_dates(first, n, day=5):
    """n dates on `day` of each month, the first in month `first` (year, month)."""
    return [date(*add_months(*first, i), day) for i in range(n)]


def charge(conn, a, days, amount, name, category="Bills & Utilities"):
    for d, amt in zip(days, amount if isinstance(amount, list) else [amount] * len(days)):
        txn(conn, a, d.isoformat(), -amt, category, name=name)


def setup(conn):
    a = account(conn, "Card", "credit")
    txn(conn, a, "2024-01-02", -5.0, "Groceries", name="Corner Store")  # history goes back well before
    return a


def find(conn, name, today=TODAY):
    return next((i for i in recurring.find(conn, today) if i["name"] == name), None)


def test_monthly_yearly_weekly_and_a_variable_bill(conn):
    a = setup(conn)
    charge(conn, a, monthly_dates((2025, 7), 12), 15.49, "Streamly")
    charge(conn, a, [date(2024, 9, 1), date(2025, 9, 3)], 99.0, "Cloud Backup")
    charge(conn, a, [TODAY - timedelta(days=7 * i) for i in range(8)], 20.0, "Lawn Crew")
    charge(conn, a, monthly_dates((2025, 7), 12, day=12), [80 + 37 * (i % 4) for i in range(12)], "Springfield Power")
    # Random shopping isn't recurring.
    charge(conn, a, [date(2026, 1, 1) + timedelta(days=d) for d in (0, 3, 4, 19, 40, 41, 77, 90, 93, 130)], 30.0, "Big Box",
           "Shopping & Personal")

    s = find(conn, "Streamly")
    assert (s["cadence"], s["typical"], s["monthly"], s["varies"]) == ("monthly", 15.49, 15.49, False)
    assert s["last"] == "2026-06-05" and s["next"] == "2026-07-05"
    y = find(conn, "Cloud Backup")
    assert y["cadence"] == "yearly" and y["monthly"] == 8.25 and y["next"] == "2026-09-03"
    w = find(conn, "Lawn Crew")
    assert w["cadence"] == "weekly" and round(w["monthly"]) == 87
    p = find(conn, "Springfield Power")
    assert p["cadence"] == "monthly" and p["varies"]
    assert find(conn, "Big Box") is None
    for i in (s, y, w, p):
        assert not (i["new"] or i["price_up"] or i["missed"] or i["stopped"]), i["name"]


def test_transfers_and_income_are_left_out(conn):
    a = setup(conn)
    charge(conn, a, monthly_dates((2025, 7), 12), 500.0, "To Savings", "Transfer")
    for d in monthly_dates((2025, 7), 12):
        txn(conn, a, d.isoformat(), 2000.0, "Paycheck", name="Payroll")
    assert recurring.find(conn, TODAY) == []


def test_new_and_price_went_up(conn):
    a = setup(conn)
    charge(conn, a, monthly_dates((2026, 4), 3), 9.99, "New Stream")
    charge(conn, a, monthly_dates((2025, 7), 12), [12.99] * 10 + [15.99] * 2, "Old Stream")
    charge(conn, a, monthly_dates((2025, 7), 12), [80, 140, 95, 120, 70, 150, 90, 85, 130, 75, 160, 140], "Gas Utility")

    new = find(conn, "New Stream")
    assert new["new"] and not new["price_up"]
    up = find(conn, "Old Stream")
    assert up["price_up"] and up["previous"] == 12.99 and up["typical"] == 15.99 and not up["new"]
    assert not find(conn, "Gas Utility")["price_up"]  # bills that vary don't count as a price increase
    # A price change long ago isn't news any more.
    later = date(2026, 11, 20)
    charge(conn, a, monthly_dates((2026, 7), 5), 15.99, "Old Stream")
    assert not find(conn, "Old Stream", later)["price_up"] and find(conn, "Old Stream", later)["typical"] == 15.99

    s = recurring.summary(conn, TODAY)
    assert (s["new"], s["price_up"]) == (1, 1)
    assert s["price_up_items"][0]["name"] == "Old Stream"


def test_one_odd_charge_is_not_a_price_increase(conn):
    a = setup(conn)
    charge(conn, a, monthly_dates((2025, 7), 12), [11.99] * 11 + [64.0], "Video Plus")
    v = find(conn, "Video Plus")
    assert v["typical"] == 11.99 and not v["price_up"] and v["last_amount"] == 64.0


def test_new_needs_older_history(conn):
    a = account(conn, "Card", "credit")
    charge(conn, a, monthly_dates((2026, 4), 3), 9.99, "New Stream")  # everything started then: can't tell
    assert not find(conn, "New Stream")["new"]


def test_missed_and_stopped(conn):
    a = setup(conn)
    charge(conn, a, monthly_dates((2025, 5), 13, day=1), 30.0, "Gym")  # last 2026-05-01
    charge(conn, a, monthly_dates((2025, 1), 10), 9.0, "Old App")  # last 2025-10-05
    gym = find(conn, "Gym")
    assert gym["missed"] and not gym["stopped"]  # 50 days: overdue by more than half a cycle
    old = find(conn, "Old App")
    assert old["stopped"] and not old["missed"]
    r = recurring.report(conn, TODAY)
    assert [i["name"] for i in r["ended"]] == ["Old App"]
    assert r["missed"] == 1 and r["monthly"] == 30.0


def test_a_membership_billed_by_a_busy_merchant(conn):
    a = setup(conn)
    # A store's random purchases plus the same membership amount every month.
    charge(conn, a, monthly_dates((2025, 9), 10, day=9), 14.99, "Mega Mart")
    charge(conn, a, [date(2025, 9, 1) + timedelta(days=d) for d in (2, 6, 13, 40, 44, 71, 100, 103, 160, 200, 230)],
           [23.5, 61.2, 8.4, 33.0, 120.7, 45.1, 18.8, 77.0, 52.3, 9.9, 64.4], "Mega Mart")
    found = [i for i in recurring.find(conn, TODAY) if i["name"] == "Mega Mart"]
    assert len(found) == 1 and found[0]["key"] == "MEGA MART|14.99" and found[0]["typical"] == 14.99


def test_ignore_and_restore(client, conn):
    a = setup(conn)
    today = date.today()
    charge(conn, a, [today - timedelta(days=30 * i + 2) for i in range(6)], 15.0, "Streamly")
    conn.commit()
    page = client.get("/recurring").get_data(as_text=True)
    assert "Streamly" in page and "1 recurring charge," in page

    r = client.post("/recurring/ignore", data={"key": "STREAMLY", "name": "Streamly"}, follow_redirects=True)
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and "Ignored (1)" in page and "Nothing recurring" not in page
    assert conn.execute("SELECT key FROM recurring_ignored").fetchall()[0][0] == "STREAMLY"
    assert recurring.report(conn)["active"] == []

    client.post("/recurring/restore", data={"key": "STREAMLY"})
    assert conn.execute("SELECT COUNT(*) FROM recurring_ignored").fetchone()[0] == 0
    assert [i["name"] for i in recurring.report(conn)["active"]] == ["Streamly"]


def test_page_on_demo_and_empty(client, conn):
    assert "No transactions yet" in client.get("/recurring").get_data(as_text=True)
    a = setup(conn)
    conn.commit()
    assert "Nothing recurring found yet" in client.get("/recurring").get_data(as_text=True)

    from budget import demo

    demo.build(conn)
    r = recurring.report(conn)
    names = {i["name"]: i for i in r["active"]}
    assert {"Netflix", "Spotify", "Disney+", "Amazon Prime"} <= set(names)
    assert names["Netflix"]["price_up"] and names["Disney+"]["new"] and names["Amazon Prime"]["cadence"] == "yearly"
    assert "Planet Fitness" in [i["name"] for i in r["ended"]]
    page = client.get("/recurring")
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert "Price went up" in text and "New" in text and "/transactions?name=Netflix" in text
    assert client.get("/recurring?cadence=yearly").status_code == 200
    assert a
