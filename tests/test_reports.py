"""Monthly and yearly money flow: what counts as income and spending, and on which date."""
import pytest

from budget import reports
from budget.rules import nearest_month_start, sync_dates

from conftest import account, category, txn


@pytest.fixture
def household(conn):
    checking, card = account(conn, "Checking"), account(conn, "Card", "credit")
    txn(conn, checking, "2026-03-01", 3000.00, "Paycheck")
    txn(conn, checking, "2026-03-15", 3000.00, "Paycheck")
    txn(conn, checking, "2026-03-20", 250.00)                        # uncategorized deposit: not income
    txn(conn, card, "2026-03-05", -120.50, "Groceries")
    txn(conn, card, "2026-03-09", 20.50, "Groceries")                # a refund nets against its category
    txn(conn, card, "2026-03-12", -45.00)                            # uncategorized spending counts
    txn(conn, checking, "2026-03-25", -800.00, "Transfer")           # card payment: neither
    txn(conn, card, "2026-03-25", 800.00, "Transfer")
    txn(conn, checking, "2026-03-26", -500.00, "Savings & Investments")
    txn(conn, checking, "2026-04-03", -60.00, "Dining & Drinks")
    txn(conn, checking, "2026-04-15", 3000.00, "Paycheck")
    txn(conn, checking, "2025-04-10", -30.00, "Dining & Drinks")
    return checking, card


def test_totals(conn, household):
    t = reports.totals(conn, *reports.bounds(2026, 3))
    assert t["income"] == 6000.00
    assert round(t["spending"], 2) == 145.00  # 120.50 - 20.50 + 45
    assert round(t["net"], 2) == 5855.00 and t["count"] == 9
    assert round(t["rate"], 4) == round(5855 / 6000, 4)
    assert reports.uncategorized_deposits(conn, *reports.bounds(2026, 3)) == {"n": 1, "total": 250.0}


def test_totals_for_one_account(conn, household):
    checking, card = household
    assert reports.totals(conn, *reports.bounds(2026, 3), accounts=[card])["spending"] == 145.00
    assert reports.totals(conn, *reports.bounds(2026, 3), accounts=[checking])["spending"] == 0
    assert reports.totals(conn, "2026-05-01", "2026-06-01")["rate"] is None


def test_monthly_and_yearly(conn, household):
    months = reports.monthly(conn, 2026, 2, 3)
    assert [(m["key"], m["income"], m["spending"], m["net"], m["has_data"]) for m in months] == [
        ("2026-02", 0.0, 0.0, 0.0, False),
        ("2026-03", 6000.0, 145.0, 5855.0, True),
        ("2026-04", 3000.0, 60.0, 2940.0, True),
    ]
    assert months[1]["label"] == "Mar"
    year = reports.totals(conn, *reports.bounds(2026))
    assert (year["income"], round(year["spending"], 2)) == (9000.0, 205.0)
    assert reports.years(conn) == [2026, 2025]


def test_monthly_across_a_year_end(conn):
    acct = account(conn, "Checking")
    txn(conn, acct, "2025-12-31", -10.0, "Groceries")
    txn(conn, acct, "2026-01-01", -20.0, "Groceries")
    assert [(m["key"], m["spending"]) for m in reports.monthly(conn, 2025, 12, 2)] == [("2025-12", 10.0), ("2026-01", 20.0)]


def test_by_category_and_merchant(conn, household):
    spend = reports.by_category(conn, *reports.bounds(2026, 3))
    assert [(c["name"], round(c["total"], 2), c["n"]) for c in spend] == [("Groceries", 100.0, 2), ("Uncategorized", 45.0, 1)]
    income = reports.by_category(conn, *reports.bounds(2026, 3), kind="income")
    assert [(c["name"], c["total"]) for c in income] == [("Paycheck", 6000.0)]
    assert reports.by_merchant(conn, *reports.bounds(2026, 3))[0]["total"] == 145.0


def test_category_matrix_and_averages(conn, household):
    rows, month_totals, active = reports.category_matrix(conn, 2026)
    assert active == [3, 4]
    dining = next(r for r in rows if r["name"] == "Dining & Drinks")
    assert dining["months"][3] == 60.0 and dining["average"] == 30.0 and dining["prev_total"] == 30.0
    assert round(month_totals[2], 2) == 145.0
    averages, n = reports.category_averages(conn, 2026, 5, months=2)
    assert n == 2 and averages[category(conn, "Dining & Drinks")] == 30.0


def test_cumulative_spending_stops_after_the_last_month(conn, household):
    series = reports.cumulative_spending(conn, [2026])[0]
    assert series["values"][:4] == [0.0, 0.0, 145.0, 205.0]
    assert series["values"][4:] == [None] * 8


@pytest.mark.parametrize("day, first", [
    ("2026-08-30", "2026-09-01"), ("2026-09-03", "2026-09-01"), ("2026-12-28", "2027-01-01"),
    ("2026-02-15", "2026-02-01"), ("2026-03-16", "2026-03-01"), ("2026-03-17", "2026-04-01"),
])
def test_nearest_month_start(day, first):
    assert nearest_month_start(day) == first


def test_rent_counts_on_the_nearest_first(conn):
    acct = account(conn, "Checking")
    mortgage = txn(conn, acct, "2026-02-27", -2100.00, "Mortgage & HOA")
    groceries = txn(conn, acct, "2026-02-27", -50.00, "Groceries")
    date_of = lambda tid: conn.execute("SELECT effective_date FROM transactions WHERE id = ?", (tid,)).fetchone()[0]
    assert date_of(mortgage) == "2026-03-01" and date_of(groceries) == "2026-02-27"
    assert reports.totals(conn, *reports.bounds(2026, 3))["spending"] == 2100.00
    assert reports.totals(conn, *reports.bounds(2026, 2))["spending"] == 50.00
    # A date set by hand wins over the snapping.
    conn.execute("UPDATE transactions SET date_override = '2026-02-28' WHERE id = ?", (mortgage,))
    sync_dates(conn)
    assert date_of(mortgage) == "2026-02-28"
    assert reports.totals(conn, *reports.bounds(2026, 2))["spending"] == 2150.00
    # And a month's latest bank activity goes by the bank's date.
    assert reports.latest_month(conn) == (2026, 2)


def test_stacked_by_category_keeps_the_rest_as_other(conn, household):
    stacked = reports.stacked_by_category(conn, 2026, top_n=1)
    assert [s["name"] for s in stacked] == ["Groceries", "Other"]
    assert stacked[0]["values"][2] == 100.0 and stacked[1]["values"][2] == 45.0 and stacked[1]["values"][3] == 60.0
