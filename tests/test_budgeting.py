"""The month scorecard, paycheck retirement savings, and the 12-month plan."""
from datetime import date

import pytest

from budget import budgeting
from budget.balances import month_end

from conftest import account, txn


def paycheck_plan(conn, start, base=4000, pct=6, rate=50, cap=4, pattern="ACME PAYROLL", account_id=None):
    conn.execute(
        """INSERT INTO paycheck_savings (pattern, account_id, start_date, base_pay, employee_pct, match_rate, match_cap_pct)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (pattern, account_id, start, base, pct, rate, cap),
    )


@pytest.mark.parametrize("pct, rate, cap, yours, employer", [
    (6, 50, 4, 240.0, 80.0),     # match stops at 4% of pay
    (3, 50, 4, 120.0, 60.0),     # under the cap: half of everything
    (10, 100, 5, 400.0, 200.0),  # dollar for dollar up to 5%
    (5, 0, 0, 200.0, 0.0),       # no match
])
def test_contribution(pct, rate, cap, yours, employer):
    got = budgeting.contribution({"base_pay": 4000, "employee_pct": pct, "match_rate": rate, "match_cap_pct": cap})
    assert tuple(round(v, 2) for v in got) == (yours, employer)


def test_work_savings_uses_the_entry_in_effect(conn):
    checking = account(conn, "Checking")
    paycheck_plan(conn, "2026-01-01")
    paycheck_plan(conn, "2026-03-10", base=5000, pct=10, rate=100, cap=5)  # a raise and a new rate
    for day in ("2025-12-31", "2026-02-27", "2026-03-06", "2026-03-20"):
        txn(conn, checking, day, 2900.0, "Paycheck", name="ACME PAYROLL PPD")
    txn(conn, checking, "2026-03-21", -2900.0, name="ACME PAYROLL REVERSAL")  # money out: not a paycheck
    txn(conn, checking, "2026-03-22", 50.0, name="SOMEONE ELSE")
    feb_mar = budgeting.work_savings(conn, "2026-02-01", "2026-04-01")
    assert feb_mar["paychecks"] == 3
    assert feb_mar["yours"] == 240 + 240 + 500 and feb_mar["employer"] == 80 + 80 + 250
    assert feb_mar["total"] == feb_mar["yours"] + feb_mar["employer"]
    before = budgeting.work_savings(conn, "2025-12-01", "2026-01-01")  # no entry yet
    assert before == {"yours": 0.0, "employer": 0.0, "total": 0.0, "paychecks": 0}
    savings = account(conn, "Savings", "savings")
    assert budgeting.work_savings(conn, "2026-02-01", "2026-04-01", accounts=[savings])["paychecks"] == 0


@pytest.fixture
def months(conn):
    """Six usual months of made-up flexible spending, then a busier March."""
    acct = account(conn, "Checking")
    usual = {"Groceries": [500, 520, 480, 510, 900, 490], "Dining & Drinks": [200, 210, 190, 205, 195, 215]}
    for i, (y, m) in enumerate([(2025, 9), (2025, 10), (2025, 11), (2025, 12), (2026, 1), (2026, 2)]):
        for cat, amounts in usual.items():
            txn(conn, acct, f"{y}-{m:02d}-10", -amounts[i], cat)
        txn(conn, acct, f"{y}-{m:02d}-01", 6000.0, "Paycheck")
    txn(conn, acct, "2026-03-01", 6000.0, "Paycheck")
    txn(conn, acct, "2026-03-02", -2100.0, "Mortgage & HOA")
    txn(conn, acct, "2026-03-03", -150.0, "Bills & Utilities")
    txn(conn, acct, "2026-03-05", -505.0, "Groceries")
    txn(conn, acct, "2026-03-07", -350.0, "Dining & Drinks")
    txn(conn, acct, "2026-03-08", -60.0, "Hobbies & Fun")
    txn(conn, acct, "2026-03-09", -1200.0, "Travel")
    txn(conn, acct, "2026-03-12", -1500.0, "Shopping & Personal", name="New Laptop", one_off=1)
    txn(conn, acct, "2026-03-13", -1100.0, "Shopping & Personal", name="Sofa")  # big, but not marked yet
    txn(conn, acct, "2026-03-20", -1000.0, "Savings & Investments")
    txn(conn, acct, "2026-03-21", -400.0, "Transfer")
    return acct


def test_by_group_pulls_one_offs_out(conn, months):
    groups, flexible = budgeting.by_group(conn, "2026-03-01", "2026-04-01")
    assert groups == {"fixed": 2250.0, "flexible": 505 + 350 + 60 + 1100.0, "nonmonthly": 1200.0, "one_off": 1500.0}
    assert {name: total for (_, name), total in flexible.items()} == {
        "Groceries": 505.0, "Dining & Drinks": 350.0, "Hobbies & Fun": 60.0, "Shopping & Personal": 1100.0}


def test_usual_month_is_the_median(conn, months):
    total, per_category, n = budgeting.typical_flexible(conn, 2026, 3)
    assert n == 6
    assert total == 710.0  # monthly totals 670, 700, 705 | 715, 730, 1095: the busy January doesn't count much
    names = {name: v for (_, name), v in per_category.items()}
    assert names == {"Groceries": 505.0, "Dining & Drinks": 202.5}
    # A month with nothing in it doesn't drag the usual month down.
    assert budgeting.typical_flexible(conn, 2026, 3, months=12)[2] == 6


def test_scorecard(conn, months):
    budgeting.set_setting(conn, "flex_target", 900)
    card = budgeting.scorecard(conn, 2026, 3)
    assert card["income"] == 6000.0
    assert card["spending"] == 2250 + 2015 + 1200 + 1500.0
    assert card["cash_flow"] == 6000 - 6965.0
    assert card["target"] == 900.0 and card["typical_flexible"] == 710.0 and card["typical_months"] == 6
    over = {d["name"]: round(d["diff"], 2) for d in card["over"]}
    assert over == {"Shopping & Personal": 1100.0, "Dining & Drinks": 147.5, "Hobbies & Fun": 60.0}
    assert card["under"] == []
    assert [(b["name"], b["amount"], b["one_off"]) for b in card["big"]] == [("New Laptop", 1500.0, 1), ("Sofa", 1100.0, 0)]
    assert card["invested"] == 1000.0
    assert card["work"]["total"] == 0 and card["rate"] == card["saved"] / 6000


def test_scorecard_counts_paycheck_savings(conn, months):
    paycheck_plan(conn, "2025-01-01", pattern="MADE UP")
    conn.execute("UPDATE transactions SET raw_description = 'MADE UP PAYROLL' WHERE amount = 6000")
    card = budgeting.scorecard(conn, 2026, 3)
    assert card["work"] == {"yours": 240.0, "employer": 80.0, "total": 320.0, "paychecks": 1}
    assert card["saved"] == -965.0 + 320.0
    assert card["rate"] == pytest.approx((-965.0 + 320.0) / 6320.0)
    rate = budgeting.savings_rate(conn, "2026-03-01", "2026-04-01")
    assert rate["work"] == 320.0 and rate["rate"] == pytest.approx(card["rate"])


def test_settings_round_trip(conn):
    assert budgeting.get_setting(conn, "plan_income") is None
    budgeting.set_setting(conn, "plan_income", 5000.5)
    budgeting.set_setting(conn, "plan_account", 3)
    assert budgeting.get_setting(conn, "plan_income") == 5000.5
    assert budgeting.get_setting(conn, "plan_account", cast=int) == 3
    budgeting.set_setting(conn, "plan_income", "not a number")
    assert budgeting.get_setting(conn, "plan_income", default=1.0) == 1.0
    budgeting.set_setting(conn, "plan_income", None)
    assert budgeting.get_setting(conn, "plan_income") is None


def test_complete_months_and_baseline(conn, months):
    got = budgeting.complete_months(conn, 3, before=date(2026, 3, 15))
    assert got == [(2025, 12), (2026, 1), (2026, 2)]
    base = budgeting.baseline(conn, got)
    assert base["regular_income"] == 6000.0
    assert base["flexible"] == pytest.approx((715 + 1095 + 705) / 3)
    assert base["fixed"] == 0 and base["one_off"] == 0
    assert budgeting.baseline(conn, []) is None


# ---------------------------------------------------------------- the plan

TODAY = date(2026, 10, 1)


def plan(conn, income=6000, fixed=3000, flex=2000, nonmonthly=500, buffer=1000, start=2000.0,
         backups=((5000.0, "Savings"), (20_000.0, "Brokerage"))):
    checking = account(conn, "Checking")
    conn.execute("INSERT INTO balances (account_id, month, amount, as_of) VALUES (?, '2026-09', ?, '2026-09-30')",
                 (checking, start))
    for key, value in (("plan_income", income), ("plan_fixed", fixed), ("flex_target", flex),
                       ("plan_nonmonthly", nonmonthly), ("plan_buffer", buffer), ("plan_account", checking)):
        budgeting.set_setting(conn, key, value)
    ids = []
    for key, (amount, name) in zip(budgeting.BACKUP_KEYS, backups):
        acct = account(conn, name, "savings")
        conn.execute("INSERT INTO balances (account_id, month, amount, as_of) VALUES (?, '2026-09', ?, '2026-09-30')",
                     (acct, amount))
        budgeting.set_setting(conn, key, acct)
        ids.append(acct)
    return checking, ids


def test_no_plan_without_income(conn):
    assert budgeting.project(conn, today=TODAY) is None


def test_plan_items_by_month(conn):
    plan(conn)
    conn.executemany("INSERT INTO plan_items (label, amount, start_month, end_month) VALUES (?, ?, ?, ?)", [
        ("Bonus", 1000, "2026-12", "2026-12"),
        ("Daycare", -800, "2027-01", None),
        ("Car paid off", 400, "2027-03", "2027-04"),
    ])
    p = budgeting.project(conn, today=TODAY)
    rows = {r["key"]: r for r in p["rows"]}
    assert [r["key"] for r in p["rows"]][:2] == ["2026-11", "2026-12"] and len(p["rows"]) == 12
    assert rows["2026-11"]["net"] == 500 and rows["2026-11"]["spend"] == 5500
    assert rows["2026-12"]["net"] == 1500 and rows["2026-12"]["income"] == 7000
    assert rows["2027-01"]["net"] == -300 and rows["2027-01"]["income"] == 6000
    assert rows["2027-03"]["net"] == 100 and rows["2027-04"]["adjust"] == -400 and rows["2027-05"]["net"] == -300
    assert p["start_balance"] == 2000.0
    assert p["average_net"] == pytest.approx(sum(r["net"] for r in p["rows"]) / 12)


def test_plan_draws_backups_in_order(conn):
    checking, (savings, brokerage) = plan(conn, income=5000, fixed=4000, flex=2500, nonmonthly=0,
                                          buffer=1000, start=2000.0, backups=((2000.0, "Savings"), (20_000.0, "Brokerage")))
    p = budgeting.project(conn, today=TODAY)
    r = p["rows"]
    # -1500 a month: Nov leaves 500, so 500 comes from Savings; Dec takes the 1500 left in Savings.
    assert (r[0]["balance"], r[0]["draws"], r[0]["left"][savings]) == (1000.0, {savings: 500.0}, 1500.0)
    assert (r[1]["balance"], r[1]["draws"]) == (1000.0, {savings: 1500.0})
    # Jan: Savings is empty, so Brokerage covers it.
    assert r[2]["draws"] == {brokerage: 1500.0} and r[2]["reserve"] == 18_500.0
    assert p["first_draw"] == "2026-11" and p["first_short"] is None
    assert p["drawn"] == {savings: 2000.0, brokerage: 1500.0 * 10}
    assert p["total_drawn"] == 17_000.0 and p["start_left"] == {savings: 2000.0, brokerage: 20_000.0}


def test_plan_runs_short_when_backups_run_out(conn):
    plan(conn, income=5000, fixed=4000, flex=2500, nonmonthly=0, buffer=1000, start=1000.0,
         backups=((1000.0, "Savings"), (500.0, "Brokerage")))
    p = budgeting.project(conn, today=TODAY)
    assert p["rows"][0]["draw"] == 1500.0 and not p["rows"][0]["short"]
    assert p["rows"][1]["draw"] == 0 and p["rows"][1]["balance"] == -500.0
    assert p["first_short"] == "2026-12"


def test_plan_account_with_no_balance_yet(conn):
    """A plan account picked before any balance was entered starts from zero instead of failing."""
    checking = account(conn, "New Checking")
    budgeting.set_setting(conn, "plan_income", 100)
    budgeting.set_setting(conn, "plan_account", checking)
    p = budgeting.project(conn, today=TODAY)
    assert p["start_balance"] == 0 and p["rows"][0]["balance"] == 100


def test_plan_backup_with_no_balance_yet(conn):
    plan(conn, backups=())
    empty = account(conn, "Empty Savings", "savings")
    budgeting.set_setting(conn, "plan_backup_account", empty)
    p = budgeting.project(conn, today=TODAY)
    assert p["total_drawn"] == 0 and p["start_left"] == {empty: 0.0}
