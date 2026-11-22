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
                       ("plan_buffer", 1000), ("plan_account", chk), ("plan_backup_account", backup),
                       (budgeting.PLAN_THROUGH, "2028-11")):
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


def test_what_would_it_take_solves_for_the_monthly_saving(conn, client):
    fund = account(conn, "Fund", "brokerage")
    cash = account(conn, "Checking")
    balance(conn, fund, "2026-10", 0.01)
    balance(conn, cash, "2026-10", 500)
    conn.commit()
    flat = {"accounts": {str(fund): {"rate": "0", "vol": "0"}}, "years": 1,
            "goal": {"amount": 12_000, "by": "2027-11", "into": str(fund), "measure": str(fund), "confidence": "expected"}}
    g = projection.project(conn, flat, TODAY, runs=20)["goal"]
    assert abs(g["needed"] - 1000) < 0.01  # 12 months of 1,000, no growth

    flat["goal"]["measure"] = "worth"  # net worth already has the 500 in checking
    assert abs(projection.project(conn, flat, TODAY, runs=20)["goal"]["needed"] - 11_500 / 12) < 0.01
    flat["goal"].update(measure=str(fund), real=True)
    p = projection.project(conn, {**flat, "inflation": "10"}, TODAY, runs=20)
    assert abs(p["goal"]["target"] - 13_200) < 0.01 and abs(p["goal"]["needed"] - 1100) < 0.01  # raised by a year of 10%

    swinging = {"accounts": {str(fund): {"rate": "7", "vol": "15"}}, "years": 1,
                "goal": {"amount": 500_000, "by": "2046-11", "into": str(fund), "measure": str(fund)}}
    answers = {}
    for level in ("expected", "0.75", "0.9"):
        swinging["goal"]["confidence"] = level
        answers[level] = projection.project(conn, swinging, TODAY)["goal"]["needed"]
    assert answers["expected"] < answers["0.75"] < answers["0.9"]
    assert 800 < answers["expected"] < 1100  # about what 7% a year needs for 20 years

    swinging["goal"].update(amount=400, measure="worth")  # the 500 in checking already covers it
    assert projection.project(conn, swinging, TODAY, runs=20)["goal"]["on_track"]

    client.post("/projection/goal", data={"amount": "$250,000", "by": "2040-01", "real": "1", "measure": "investments",
                                          "into": str(fund), "confidence": "0.9"})
    saved = projection.load_setup(conn)["goal"]
    assert saved["amount"] == 250_000 and saved["real"] and saved["confidence"] == "0.9"
    html = client.get("/projection").get_data(as_text=True)
    assert "a month</b> into Fund" in html and "in 9 of 10 futures" in html and "How this works" in html
    client.post("/projection/goal", data={"clear": "1"})
    assert "goal" not in projection.load_setup(conn)


def test_how_bumpy_drawdowns_chance_behind_and_n0(conn, client):
    fund = account(conn, "Fund", "brokerage")
    balance(conn, fund, "2026-10", 100_000)
    conn.commit()
    p = projection.project(conn, {"accounts": {str(fund): {"rate": "7", "vol": "15"}}, "years": 20}, TODAY)
    mix = p["mix"]
    assert abs(mix["n0"] - 7.1) < 0.2  # (0.15 / (ln 1.07 - 0.15^2 / 2))^2
    assert mix["behind_by_year"][1] > mix["behind_by_year"][5] > mix["behind_by_year"][20] > 0
    assert 0.1 < mix["drop_typical"] < mix["drop_bad"] < 0.8
    assert p["rows"][0]["behind"] > p["rows"][-1]["behind"]
    html = client.get("/projection").get_data(as_text=True)
    assert "How bumpy the ride is" in html and "N0" in html


def test_with_the_plan_the_projection_stops_at_the_plans_end(conn, client):
    chk = account(conn, "Checking")
    fund = account(conn, "Fund", "brokerage")
    balance(conn, chk, "2026-10", 2000)
    balance(conn, fund, "2026-10", 1000)
    for key, value in (("plan_income", 5000), ("plan_fixed", 3000), ("flex_target", 1500), ("plan_account", chk)):
        budgeting.set_setting(conn, key, value)
    conn.commit()
    setup = {"years": 20, "accounts": {str(fund): {"rate": "0", "vol": "0"}}}
    p = projection.project(conn, setup, TODAY, runs=5)  # no end set: the next 12 months
    assert p["plan_end"] == "2027-11" and [r["key"] for r in p["rows"]] == ["2027-11"]
    assert p["rows"][0]["accounts"][chk] == 2000 + 12 * 500

    client.post("/plan/settings", data={"plan_through": "2030-05"})
    p = projection.project(conn, setup, TODAY, runs=5)
    assert [r["key"] for r in p["rows"]] == ["2027-11", "2028-11", "2029-11", "2030-05"] and p["rows"][-1]["partial"]
    assert p["rows"][-1]["accounts"][chk] == 2000 + 42 * 500
    assert len(projection.project(conn, {**setup, "plan": False}, TODAY, runs=5)["rows"]) == 20  # growth alone: the years picked
    assert len(budgeting.project(conn, today=TODAY)["rows"]) == 42

    setup["goal"] = {"amount": 50_000, "by": "2035-01", "into": str(fund), "measure": "worth"}
    p = projection.project(conn, setup, TODAY, runs=5)
    assert p["goal"] is None and p["goal_error"]
    html = client.get("/projection").get_data(as_text=True)
    assert "when your Plan ends" in html and "projection stops there" in html
    plan_html = client.get("/plan").get_data(as_text=True)
    assert "Through May 2030" in plan_html and 'name="plan_through" value="2030-05"' in plan_html
    client.post("/plan/settings", data={"plan_through": ""})
    assert budgeting.plan_end(conn, TODAY) == ("2027-11", 12, False)
