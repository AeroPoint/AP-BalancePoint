"""The app's forms and JSON endpoints, posted through the test client against made-up data."""
from datetime import date

import pytest

from budget import budgeting
from budget.balances import load_ledgers

from conftest import account, category, txn


def one(conn, sql, *params):
    row = conn.execute(sql, params).fetchone()
    return row[0] if row is not None and len(row) == 1 else row


def post(client, url, **data):
    r = client.post(url, data=data, follow_redirects=True)
    assert r.status_code == 200, url
    return r.get_data(as_text=True)


# ---------------------------------------------------------------- accounts

def test_account_save_and_edit(conn, client):
    post(client, "/accounts/save", name="  Joint   Checking ", kind="checking", institution="Made Up Bank",
         opened="2024-01", bank_from="2025-06")
    a = conn.execute("SELECT * FROM accounts WHERE name = 'Joint Checking'").fetchone()
    assert (a["kind"], a["institution"], a["opened"], a["closed"], a["bank_from"]) == (
        "checking", "Made Up Bank", "2024-01", None, "2025-06")
    post(client, "/accounts/save", id=a["id"], name="Joint", kind="no-such-kind", opened="2024-01", closed="2026-13")
    a = conn.execute("SELECT * FROM accounts WHERE id = ?", (a["id"],)).fetchone()
    assert (a["name"], a["kind"], a["closed"], a["bank_from"]) == ("Joint", "other", None, None)


def test_account_save_refuses_bad_input(conn, client):
    assert "can&#39;t close before" in post(client, "/accounts/save", name="Card", opened="2025-05", closed="2025-04")
    post(client, "/accounts/save", name="")
    post(client, "/accounts/save", name="Card", kind="credit")
    assert "already an account" in post(client, "/accounts/save", name="Card", kind="credit")
    assert one(conn, "SELECT COUNT(*) FROM accounts") == 1


def test_close_account(conn, client):
    acct = account(conn, "Old Card", "credit")
    conn.commit()
    post(client, f"/accounts/{acct}/close", month="2025-07")
    assert one(conn, "SELECT closed FROM accounts WHERE id = ?", acct) == "2025-07"
    post(client, f"/accounts/{acct}/close", month="whenever")
    assert one(conn, "SELECT closed FROM accounts WHERE id = ?", acct) == date.today().strftime("%Y-%m")


def test_delete_account_only_without_transactions(conn, client):
    empty, used = account(conn, "Empty", "savings"), account(conn, "Used")
    conn.execute("INSERT INTO balances (account_id, month, amount, as_of) VALUES (?, '2026-01', 5, '2026-01-31')", (empty,))
    txn(conn, used, "2026-01-05", -10.0)
    conn.commit()
    post(client, f"/accounts/{empty}/delete")
    assert "has transactions" in post(client, f"/accounts/{used}/delete")
    assert [r[0] for r in conn.execute("SELECT name FROM accounts")] == ["Used"]
    assert one(conn, "SELECT COUNT(*) FROM balances") == 0


def test_merge_accounts(conn, client):
    old, new = account(conn, "Old Checking"), account(conn, "Checking")
    from budget.csv_import import store_transactions

    row = {"date": "2026-01-05", "amount": -20.0, "raw": "SAFEWAY", "memo": None, "mcc": None}
    store_transactions(conn, old, "a.csv", [row, {**row, "date": "2026-01-06"}])
    store_transactions(conn, new, "b.csv", [row])  # the same charge was uploaded to both
    conn.execute("INSERT INTO balances (account_id, month, amount, as_of) VALUES (?, '2026-01', 500, '2026-01-10')", (old,))
    conn.execute("INSERT INTO loan_terms (account_id, principal, annual_rate, term_months, start_date) "
                 "VALUES (?, 1000, 0, 10, '2026-01-01')", (old,))
    conn.commit()
    html = post(client, f"/accounts/{old}/merge", target=new)
    assert "moved 1 transactions and dropped 1" in html
    assert one(conn, "SELECT COUNT(*) FROM accounts WHERE id = ?", old) == 0
    keys = sorted(r[0] for r in conn.execute("SELECT dedupe_key FROM transactions WHERE account_id = ?", (new,)))
    assert len(keys) == 2 and all(k.startswith(f"{new}|") for k in keys)
    # Uploading the old export to the new account now finds them all.
    assert store_transactions(conn, new, "a.csv", [row, {**row, "date": "2026-01-06"}])[2] == 0
    assert one(conn, "SELECT COUNT(*) FROM loan_terms WHERE account_id = ?", new) == 1
    # The balance keeps the day it was true on.
    assert one(conn, "SELECT as_of FROM balances WHERE account_id = ?", new) == "2026-01-10"
    assert "Pick a different account" in post(client, f"/accounts/{new}/merge", target=new)


def test_business_account_defaults_recategorize(conn, client):
    studio = account(conn, "Studio")
    from budget.csv_import import store_transactions

    store_transactions(conn, studio, "s.csv", [
        {"date": "2026-02-01", "amount": -30.0, "raw": "SAFEWAY #1", "memo": None, "mcc": None},
        {"date": "2026-02-02", "amount": 400.0, "raw": "CLIENT DEPOSIT", "memo": None, "mcc": None},
        {"date": "2026-02-03", "amount": -90.0, "raw": "PAYMENT THANK YOU", "memo": None, "mcc": None},
    ])
    picked = conn.execute("SELECT id FROM transactions WHERE raw_description = 'SAFEWAY #1'").fetchone()[0]
    conn.commit()
    income, costs = category(conn, "Side Income"), category(conn, "Shopping & Personal")
    assert "2 transactions re-categorized" in post(client, "/accounts/defaults", account_id=studio,
                                                     default_in_category=income, default_out_category=costs)
    got = {r[0]: r[1] for r in conn.execute("SELECT raw_description, category_id FROM transactions")}
    assert got == {"SAFEWAY #1": costs, "CLIENT DEPOSIT": income, "PAYMENT THANK YOU": category(conn, "Transfer")}
    # Clearing them puts the rules back, except on a row picked by hand.
    conn.execute("UPDATE transactions SET category_id = ?, category_source = 'manual' WHERE id = ?",
                 (category(conn, "Travel"), picked))
    conn.commit()
    post(client, "/accounts/defaults", account_id=studio, default_in_category="", default_out_category="")
    got = {r[0]: r[1] for r in conn.execute("SELECT raw_description, category_id FROM transactions")}
    assert got["SAFEWAY #1"] == category(conn, "Travel") and got["CLIENT DEPOSIT"] != income
    assert one(conn, "SELECT default_in_category FROM accounts WHERE id = ?", studio) is None


def test_loan_and_paycheck_entries(conn, client):
    mortgage, k401 = account(conn, "Mortgage", "loan"), account(conn, "401k", "retirement")
    conn.commit()
    assert "$1,199.10 a month" in post(client, "/loans/save", account_id=mortgage, principal="$200,000",
                                        annual_rate="6%", years="30", start_date="2020-01-15")
    loan = conn.execute("SELECT * FROM loan_terms").fetchone()
    assert (loan["principal"], loan["annual_rate"], loan["term_months"], loan["counts_from"]) == (200_000, 6, 360, None)
    assert load_ledgers(conn)[mortgage].on("2021-01-15") == 197_543.98
    assert "needs its account" in post(client, "/loans/save", account_id=mortgage, principal="0", annual_rate="6", years="30",
                                       start_date="2020-01-15")
    html = post(client, "/paychecks/save", pattern=" acme  payroll ", account_id=k401, base_pay="4,000", employee_pct="6%",
                match_rate="50", match_cap_pct="4", start_date="2026-01-01")
    assert "$240.00 of yours and $80.00 from your employer" in html
    assert one(conn, "SELECT pattern FROM paycheck_savings") == "ACME PAYROLL"


# ---------------------------------------------------------------- categories

def test_category_save_with_group_and_flag(conn, client):
    post(client, "/categories/save", name=" Garden  Supplies ", kind="expense", grp="nonmonthly", flag="  Rental   property ")
    c = conn.execute("SELECT * FROM categories WHERE name = 'Garden Supplies'").fetchone()
    assert (c["kind"], c["grp"], c["flag"], c["snap_to_month"]) == ("expense", "nonmonthly", "Rental property", 0)
    post(client, "/categories/save", id=c["id"], name="Garden Supplies", kind="transfer", grp="nonmonthly", flag="")
    c = conn.execute("SELECT * FROM categories WHERE id = ?", (c["id"],)).fetchone()
    assert (c["kind"], c["grp"], c["flag"]) == ("transfer", None, None)
    post(client, "/categories/save", id=c["id"], name="Garden Supplies", kind="expense", grp="bogus")
    assert one(conn, "SELECT grp FROM categories WHERE id = ?", c["id"]) == "flexible"
    assert "already a category" in post(client, "/categories/save", name="Groceries", kind="expense")
    assert "needs a name and a type" in post(client, "/categories/save", name="X", kind="other")


def test_category_snap_moves_existing_transactions(conn, client):
    acct = account(conn, "Checking")
    tid = txn(conn, acct, "2026-03-30", 1500.0, "Side Income")
    conn.commit()
    cid = category(conn, "Side Income")
    post(client, "/categories/save", id=cid, name="Side Income", kind="income", snap_to_month="1")
    assert one(conn, "SELECT effective_date FROM transactions WHERE id = ?", tid) == "2026-04-01"


def test_category_merge_and_delete(conn, client):
    acct = account(conn, "Checking")
    a = txn(conn, acct, "2026-03-03", -10.0, "Hobbies & Fun")
    b = txn(conn, acct, "2026-03-28", -20.0, "Hobbies & Fun")
    conn.execute("INSERT INTO rules (match_on, pattern, category_id, source) VALUES ('raw', 'MADE UP SHOP', ?, 'user')",
                 (category(conn, "Hobbies & Fun"),))
    conn.commit()
    source, target = category(conn, "Hobbies & Fun"), category(conn, "Mortgage & HOA")
    assert "(2 transactions)" in post(client, "/categories/merge", source=source, target=target)
    assert {r[0] for r in conn.execute("SELECT category_id FROM transactions")} == {target}
    assert one(conn, "SELECT category_id FROM rules WHERE pattern = 'MADE UP SHOP'") == target
    assert one(conn, "SELECT effective_date FROM transactions WHERE id = ?", b) == "2026-04-01"  # now snaps
    assert "Pick two different" in post(client, "/categories/merge", source=target, target=target)

    shop = txn(conn, acct, "2026-03-04", -5.0, "Shopping & Personal", name="SAFEWAY 12", category_source="rule")
    conn.commit()
    post(client, f"/categories/{category(conn, 'Shopping & Personal')}/delete")
    assert one(conn, "SELECT COUNT(*) FROM categories WHERE name = 'Shopping & Personal'") == 0
    row = conn.execute("SELECT c.name, t.category_source FROM transactions t LEFT JOIN categories c ON c.id = t.category_id "
                       "WHERE t.id = ?", (shop,)).fetchone()
    assert tuple(row) == ("Groceries", "rule")  # re-run through the rules
    assert one(conn, "SELECT category_id FROM transactions WHERE id = ?", a) == target


# ---------------------------------------------------------------- rules

def test_rule_save_reapplies_and_delete_undoes(conn, client):
    acct = account(conn, "Checking")
    from budget.csv_import import store_transactions

    store_transactions(conn, acct, "a.csv", [
        {"date": "2026-01-05", "amount": -12.0, "raw": "ZZQ MADEUP STORE 0042", "memo": None, "mcc": None},
        {"date": "2026-01-06", "amount": -99.0, "raw": "ZZQ MADEUP STORE 0042", "memo": None, "mcc": None},
    ])
    conn.commit()
    pets = category(conn, "Pets")
    html = post(client, "/rules/save", match_on="raw", pattern="zzq   madeup", rename_to="Made Up Pets", category_id=pets)
    assert "2 transactions updated" in html
    rule = conn.execute("SELECT * FROM rules WHERE pattern = 'ZZQ MADEUP'").fetchone()
    assert (rule["rename_to"], rule["category_id"], rule["source"], rule["amount"]) == ("Made Up Pets", pets, "user", None)
    assert {tuple(r) for r in conn.execute("SELECT name, category_id FROM transactions")} == {("Made Up Pets", pets)}
    # An amount-specific rule for the same text wins for that amount only.
    vet = category(conn, "Health & Fitness")
    post(client, "/rules/save", match_on="raw", pattern="ZZQ MADEUP", category_id=vet, amount="-99")
    assert one(conn, "SELECT category_id FROM transactions WHERE amount = -99") == vet
    assert one(conn, "SELECT category_id FROM transactions WHERE amount = -12") == pets
    assert "already exists" in post(client, "/rules/save", match_on="raw", pattern="ZZQ MADEUP", category_id=vet, amount="-99")
    assert "invalid" in post(client, "/rules/save", match_on="raw", pattern="re:(", category_id=vet)
    assert "a new name, a category" in post(client, "/rules/save", match_on="raw", pattern="ANYTHING")
    for rid in [r[0] for r in conn.execute("SELECT id FROM rules WHERE pattern = 'ZZQ MADEUP'")]:
        post(client, f"/rules/{rid}/delete")
    assert {r[0] for r in conn.execute("SELECT category_id FROM transactions")} == {None}


def test_rules_page_tests_a_description(conn, client):
    html = client.get("/rules?test=SAFEWAY%20%231234%20SPRINGFIELD%20CO").get_data(as_text=True)
    assert "Safeway" in html and "Groceries" in html


# ---------------------------------------------------------------- transactions

def test_add_by_hand_and_delete(conn, client):
    cash = account(conn, "Cash", "cash")
    conn.commit()
    post(client, "/transactions/add", account_id=cash, date="2026-03-04", name=" Farmers  market ", amount="$25.50",
         direction="out", category_id=category(conn, "Groceries"), notes="eggs")
    post(client, "/transactions/add", account_id=cash, date="2026-03-05", name="Birthday money", amount="-40", direction="in")
    post(client, "/transactions/add", account_id=cash, date="2026-02-27", name="Mortgage top-up", amount="100",
         direction="out", category_id=category(conn, "Mortgage & HOA"))
    got = {r["name"]: dict(r) for r in conn.execute("SELECT * FROM transactions")}
    market = got["Farmers market"]
    assert (market["amount"], market["category_source"], market["notes"], market["name_locked"]) == (-25.5, "manual", "eggs", 1)
    assert got["Birthday money"]["amount"] == 40.0 and got["Birthday money"]["category_source"] in ("none", "rule", "mcc")
    assert got["Mortgage top-up"]["effective_date"] == "2026-03-01"
    assert "needs an account" in post(client, "/transactions/add", account_id=cash, date="nope", name="x", amount="1")
    assert one(conn, "SELECT COUNT(*) FROM transactions") == 3

    post(client, f"/transactions/{market['id']}/delete")
    assert one(conn, "SELECT COUNT(*) FROM transactions WHERE id = ?", market["id"]) == 0
    from budget.csv_import import store_transactions

    store_transactions(conn, cash, "a.csv", [{"date": "2026-03-06", "amount": -1.0, "raw": "BANK ROW", "memo": None, "mcc": None}])
    conn.commit()
    bank = one(conn, "SELECT id FROM transactions WHERE raw_description = 'BANK ROW'")
    assert "Only transactions you added by hand" in post(client, f"/transactions/{bank}/delete")
    assert one(conn, "SELECT COUNT(*) FROM transactions WHERE id = ?", bank) == 1


@pytest.fixture
def bank_rows(conn):
    acct = account(conn, "Checking")
    from budget.csv_import import store_transactions

    store_transactions(conn, acct, "a.csv", [
        {"date": "2026-03-01", "amount": -8.0, "raw": "QQX MADEUP SHOP", "memo": None, "mcc": None},
        {"date": "2026-03-08", "amount": -9.0, "raw": "QQX MADEUP SHOP", "memo": None, "mcc": None},
        {"date": "2026-03-09", "amount": -60.0, "raw": "SAFEWAY #9", "memo": None, "mcc": None},
    ])
    conn.commit()
    return [r[0] for r in conn.execute("SELECT id FROM transactions ORDER BY date")]


def api(client, tid, **data):
    r = client.post(f"/api/transactions/{tid}", json=data)
    return r.status_code, r.get_json()


def test_api_rename_one_then_everywhere(conn, client, bank_rows):
    first, second, _ = bank_rows
    old = one(conn, "SELECT name FROM transactions WHERE id = ?", first)
    status, body = api(client, first, name="  Made Up  Cafe ")
    assert status == 200 and body["transaction"]["name"] == "Made Up Cafe"
    assert one(conn, "SELECT name_locked FROM transactions WHERE id = ?", first) == 1
    assert one(conn, "SELECT name FROM transactions WHERE id = ?", second) == old
    status, body = api(client, first, name="Made Up Cafe", previous_name=old, remember=True)
    assert body["changed"] >= 1
    assert one(conn, "SELECT name FROM transactions WHERE id = ?", second) == "Made Up Cafe"
    # New rows from that merchant get the name too.
    from budget.rules import RuleEngine

    assert RuleEngine(conn).resolve("QQX MADEUP SHOP #4").name == "Made Up Cafe"


def test_api_category_once_and_remembered(conn, client, bank_rows):
    first, second, safeway = bank_rows
    dining = category(conn, "Pets")
    _, body = api(client, first, category_id=dining)
    assert (body["transaction"]["category_name"], body["transaction"]["category_source"]) == ("Pets", "manual")
    assert one(conn, "SELECT category_id FROM transactions WHERE id = ?", second) != dining
    travel = category(conn, "Travel")
    _, body = api(client, second, category_id=travel, remember=True)
    assert body["transaction"]["category_source"] == "rule"
    assert one(conn, "SELECT category_id FROM transactions WHERE id = ?", first) == dining  # picked by hand stays
    assert one(conn, "SELECT category_id FROM rules WHERE match_on = 'name' AND pattern = ?",
               one(conn, "SELECT name FROM transactions WHERE id = ?", second)) == travel
    _, body = api(client, safeway, category_id=None)
    assert (body["transaction"]["category_id"], body["transaction"]["category_source"]) == (None, "none")


def test_api_date_notes_one_off_and_flag(conn, client, bank_rows):
    tid = bank_rows[2]
    status, body = api(client, tid, date="2026-02-28")
    assert status == 200 and body["transaction"]["effective_date"] == "2026-02-28" and body["transaction"]["date"] == "2026-03-09"
    assert api(client, tid, date="28/02/2026")[0] == 400
    _, body = api(client, tid, date="")
    assert body["transaction"]["effective_date"] == "2026-03-09" and body["transaction"]["date_override"] is None
    _, body = api(client, tid, notes="  split with Sam ")
    assert body["transaction"]["notes"] == "split with Sam"
    api(client, tid, one_off=True, flag="check")
    assert tuple(conn.execute("SELECT one_off, flag FROM transactions WHERE id = ?", (tid,)).fetchone()) == (1, "check")
    api(client, tid, one_off=False, flag="whatever")
    assert tuple(conn.execute("SELECT one_off, flag FROM transactions WHERE id = ?", (tid,)).fetchone()) == (0, None)
    assert api(client, 999_999, notes="x")[0] == 404


# ---------------------------------------------------------------- net worth and the plan

def test_net_worth_balance_entry(conn, client):
    checking, card = account(conn, "Checking"), account(conn, "Card", "credit")
    closed = account(conn, "Closed", "savings")
    conn.execute("UPDATE accounts SET closed = '2025-01' WHERE id = ?", (closed,))
    conn.commit()
    html = post(client, "/net-worth", month="2026-02", **{f"acct-{checking}": "$1,234.56", f"acct-{card}": "(50)",
                                                           f"acct-{closed}": "999"})
    assert "Saved 2 balances" in html
    got = {r[0]: (r[1], r[2], r[3]) for r in conn.execute("SELECT account_id, amount, as_of, source FROM balances")}
    assert got == {checking: (1234.56, "2026-02-28", "manual"), card: (-50.0, "2026-02-28", "manual")}
    post(client, "/net-worth", month="2026-02", as_of="2026-02-28", **{f"acct-{checking}": "", f"had-{checking}": "1",
                                                                      f"acct-{card}": ""})
    assert one(conn, "SELECT COUNT(*) FROM balances WHERE account_id = ?", checking) == 0
    assert one(conn, "SELECT COUNT(*) FROM balances WHERE account_id = ?", card) == 1
    post(client, "/net-worth", as_of="2026-01-15", **{f"acct-{checking}": "700"})
    assert tuple(conn.execute("SELECT month, as_of FROM balances WHERE account_id = ?", (checking,)).fetchone()) == (
        "2026-01", "2026-01-15")


def test_plan_settings_and_items(conn, client):
    checking, savings = account(conn, "Checking"), account(conn, "Savings", "savings")
    conn.commit()
    post(client, "/plan/settings", plan_income="$6,000", plan_fixed="3000", flex_target="1,500.555", plan_nonmonthly="",
         plan_buffer="500", plan_account=checking, plan_backup_account=savings, plan_backup_account_2="")
    get = lambda k, cast=float: budgeting.get_setting(conn, k, cast=cast)
    assert (get("plan_income"), get("plan_fixed"), get("flex_target"), get("plan_nonmonthly"), get("plan_buffer")) == (
        6000.0, 3000.0, 1500.56, None, 500.0)
    assert (get("plan_account", int), get("plan_backup_account", int), get("plan_backup_account_2", int)) == (checking, savings, None)
    post(client, "/plan/settings", plan_fixed="3100")  # fields left out stay as they were
    assert get("plan_income") == 6000.0 and get("plan_fixed") == 3100.0

    post(client, "/plan/items/save", label="Daycare", amount="800", direction="less", start_month="2027-01", end_month="")
    post(client, "/plan/items/save", label="Raise", amount="300", direction="more", start_month="2027-03", end_month="2027-13")
    items = {r["label"]: dict(r) for r in conn.execute("SELECT * FROM plan_items")}
    assert (items["Daycare"]["amount"], items["Daycare"]["end_month"]) == (-800.0, None)
    assert (items["Raise"]["amount"], items["Raise"]["end_month"]) == (300.0, None)
    assert "needs a name" in post(client, "/plan/items/save", label="", amount="5", start_month="2027-01")
    post(client, "/plan/items/save", id=items["Raise"]["id"], label="Raise", amount="350", start_month="2027-04", end_month="2027-06")
    assert tuple(conn.execute("SELECT amount, start_month, end_month FROM plan_items WHERE label = 'Raise'").fetchone()) == (
        350.0, "2027-04", "2027-06")
    # The plan page renders with a plan account that has no balance yet.
    html = client.get("/plan").get_data(as_text=True)
    assert "Daycare" in html and "Traceback" not in html
    post(client, f"/plan/items/{items['Daycare']['id']}/delete")
    assert one(conn, "SELECT COUNT(*) FROM plan_items") == 1


def test_flex_target(conn, client):
    post(client, "/settings/flex-target", flex_target="$1,200")
    assert budgeting.get_setting(conn, "flex_target") == 1200.0
    post(client, "/settings/flex-target", flex_target="0")
    assert budgeting.get_setting(conn, "flex_target") is None
