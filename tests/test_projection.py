"""Projection page: growth rates from history or defaults, the likely range, and the Plan running through it
(made-up data)."""
from datetime import date

from budget import budgeting, projection
from budget.reports import add_months

from conftest import account

TODAY = date(2026, 11, 15)


def balance(conn, acct, month, amount):
    conn.execute("INSERT INTO balances (account_id, month, amount, as_of) VALUES (?, ?, ?, ?)",
                 (acct, month, amount, f"{month}-28"))


def history(conn, acct, months, start=10_000.0, monthly=0.005, jump_at=None):
    y, m, amount = 2023, 10, start
    for i in range(months):
        balance(conn, acct, f"{y}-{m:02d}", round(amount, 2))
        y, m = add_months(y, m, 1)
        amount *= 1 + monthly
        if i == jump_at:
            amount += 5000  # money moved in that nothing recorded: not growth


def test_history_sets_the_rate_only_with_three_years_and_skips_jumps(conn):
    long_one = account(conn, "Brokerage A", "brokerage")
    short_one = account(conn, "Brokerage B", "brokerage")
    history(conn, long_one, 38, jump_at=10)
    history(conn, short_one, 20)
    rate, vol, used = projection.history_rate(conn, long_one, {})
    assert used == 36 and abs(rate - 6.2) < 0.1 and vol < 0.5  # 0.5% a month, the jump left out
    accts = {a["name"]: a for a in projection.accounts(conn, {}, TODAY)}
    assert accts["Brokerage A"]["rate"] == rate and "from 36 months" in accts["Brokerage A"]["source"]
    assert accts["Brokerage B"]["rate"] == 7.0 and "36 are needed" in accts["Brokerage B"]["source"]


def test_expected_growth_range_and_settings(conn, client):
    fund = account(conn, "Fund", "brokerage")
    cash = account(conn, "Checking")
    balance(conn, fund, "2026-10", 10_000)
    balance(conn, cash, "2026-10", 1_000)
    conn.commit()
    setup = {"years": 2, "accounts": {str(fund): {"rate": "10", "vol": "0"}}}
    p = projection.project(conn, setup, TODAY, runs=50)
    assert [r["year"] for r in p["rows"]] == [2027, 2028]
    assert abs(p["rows"][0]["worth"] - 12_000) < 1 and abs(p["rows"][1]["worth"] - 13_100) < 1
    assert p["rows"][1]["low"] == p["rows"][1]["high"] == p["rows"][1]["mid"]  # nothing swings: no range

    setup["accounts"][str(fund)]["vol"] = "20"
    p = projection.project(conn, setup, TODAY, runs=200)
    last = p["rows"][-1]
    assert last["low"] < last["worth"] < last["high"]
    assert projection.project(conn, setup, TODAY, runs=200)["rows"][-1]["low"] == last["low"]  # same seed, same page

    setup.update(real=True, inflation="10")
    assert abs(projection.project(conn, setup, TODAY, runs=10)["rows"][0]["worth"] - (10_000 * 1.1 + 1_000) / 1.1) < 1
    setup = {"rate_mode": "global", "global_rate": "0", "global_vol": "0", "accounts": {str(cash): {"on": False}}}
    p = projection.project(conn, setup, TODAY, runs=10)
    assert [a["name"] for a in p["used"]] == ["Fund"] and abs(p["rows"][0]["worth"] - 10_000) < 0.01

    client.post("/projection", data={"years": "5", "account": [str(fund), str(cash)], f"on-{fund}": "1",
                                     f"rate-{fund}": "4", "rate_mode": "account"})
    saved = projection.load_setup(conn)
    assert saved["years"] == "5" and saved["accounts"][str(cash)]["on"] is False
    html = client.get("/projection").get_data(as_text=True)
    assert "Net worth by year" in html and "set by you: 4.0%" in html and 'href="/projection"' in html


def test_paychecks_keep_contributing_and_loans_pay_down(conn):
    k401 = account(conn, "401k", "retirement")
    loan = account(conn, "Car Loan", "loan")
    chk = account(conn, "Checking")
    balance(conn, k401, "2026-10", 50_000)
    conn.execute("""INSERT INTO paycheck_savings (pattern, account_id, start_date, base_pay, employee_pct, match_rate, match_cap_pct)
                    VALUES ('ACME PAYROLL', ?, '2025-01-01', 1000, 10, 50, 10)""", (k401,))
    for month in range(1, 13):
        y, m = add_months(2025, 11, month - 1)
        conn.execute("""INSERT INTO transactions (account_id, date, effective_date, amount, name, raw_description, dedupe_key)
                        VALUES (?, ?, ?, 3000, 'Paycheck', 'ACME PAYROLL', ?)""", (chk, f"{y}-{m:02d}-15", f"{y}-{m:02d}-15", f"p{month}"))
    conn.execute("""INSERT INTO loan_terms (account_id, label, principal, annual_rate, term_months, start_date)
                    VALUES (?, 'car', 12000, 0, 24, '2026-01-01')""", (loan,))
    conn.commit()
    setup = {"accounts": {str(k401): {"rate": "0", "vol": "0"}}, "years": 2}
    p = projection.project(conn, setup, TODAY, runs=5)
    acct = {a["name"]: a for a in p["accounts"]}
    assert acct["401k"]["monthly_in"] == 150  # 10% of 1,000 plus half of it matched
    assert abs(p["rows"][0]["accounts"][k401] - (50_000 + 12 * 150)) < 0.01
    assert p["rows"][1]["accounts"][loan] == 0  # the 24-month loan is paid off by the end of 2027


def test_the_plan_runs_through_it_with_backups_and_the_savings_goal(conn):
    chk = account(conn, "Checking")
    backup = account(conn, "Savings", "savings")
    goal = account(conn, "Fund", "brokerage")
    for acct, amount in ((chk, 2000), (backup, 6000), (goal, 0)):
        balance(conn, acct, "2026-10", amount)
    for key, value in (("plan_income", 5000), ("plan_fixed", 3000), ("flex_target", 2500), ("plan_nonmonthly", 0),
                       ("plan_buffer", 1000), ("plan_account", chk), ("plan_backup_account", backup)):
        budgeting.set_setting(conn, key, value)
    conn.commit()
    zero = {str(a): {"rate": "0", "vol": "0"} for a in (chk, backup, goal)}
    p = projection.project(conn, {"years": 2, "accounts": zero}, TODAY, runs=5)  # 500 short a month
    assert p["with_plan"] and p["first_short"] == "2028-02"  # 2 months down to the cushion, then 12 drawing 500 from 6,000
    assert p["rows"][0]["accounts"][backup] == 6000 - 5000
    off = projection.project(conn, {"years": 2, "plan": False, "accounts": zero}, TODAY, runs=5)
    assert not off["with_plan"] and off["rows"][-1]["accounts"][chk] == 2000

    budgeting.set_setting(conn, "flex_target", 1500)
    for key, value in ((budgeting.PLAN_MODE, "savings"), ("plan_savings", 500), ("plan_savings_account", goal)):
        budgeting.set_setting(conn, key, value)
    p = projection.project(conn, {"years": 1, "accounts": zero}, TODAY, runs=5)  # 5000 - 3000 - 500 saved = 1500 flexible
    assert p["first_short"] is None and p["rows"][0]["accounts"][goal] == 6000 and p["savings"] == 500
