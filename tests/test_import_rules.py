"""CSV parsing and de-duplication, categorizing rules, business accounts and category checkboxes."""
from budget.csv_import import parse_csv, store_transactions
from budget.rules import RuleEngine, reapply

from conftest import account, category

US_BANK = """Date,Transaction,Name,Memo,Amount
2026-03-02,DEBIT,SAFEWAY #1234 SPRINGFIELD CO,24692163092; 05411; ; ; ;,-54.21
2026-03-02,DEBIT,SAFEWAY #1234 SPRINGFIELD CO,24692163093; 05411; ; ; ;,-54.21
2026-03-03,CREDIT,ACME CORP PAYROLL,,2650.00
2026-03-05,DEBIT,SQ *NEW PLACE,24692163094; 05812; ; ; ;,-12.50
"""


def test_parse_us_bank_export():
    rows = parse_csv(US_BANK)
    assert [r["amount"] for r in rows] == [-54.21, -54.21, 2650.0, -12.5]
    assert rows[0]["mcc"] == "5411" and rows[3]["mcc"] == "5812"


def test_parse_debit_credit_columns():
    rows = parse_csv("Posted Date,Description,Debit,Credit\n03/04/2026,COFFEE,4.50,\n03/05/2026,REFUND,,20.00\n")
    assert [(r["date"], r["amount"]) for r in rows] == [("2026-03-04", -4.5), ("2026-03-05", 20.0)]


def test_reupload_skips_duplicates_but_keeps_same_day_repeats(conn):
    acct = account(conn, "Checking")
    _, read, added, _, _ = store_transactions(conn, acct, "a.csv", parse_csv(US_BANK))
    assert (read, added) == (4, 4)  # two identical Safeway charges on one day are both real
    _, read, added, _, _ = store_transactions(conn, acct, "b.csv", parse_csv(US_BANK))
    assert added == 0


def test_rules_and_card_type_guess(conn):
    acct = account(conn, "Checking")
    store_transactions(conn, acct, "a.csv", parse_csv(US_BANK))
    got = {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT t.name, c.name, t.category_source FROM transactions t LEFT JOIN categories c ON c.id = t.category_id")}
    assert got["Safeway"] == ("Groceries", "rule")
    assert got["NEW Place"][1] == "mcc"  # no rule: guessed from the card's merchant type (5812, restaurants)


def test_business_account_defaults(conn):
    biz = account(conn, "Studio")
    home = account(conn, "Joint")
    income, costs = category(conn, "Side Income"), category(conn, "Shopping & Personal")
    conn.execute("UPDATE accounts SET default_in_category = ?, default_out_category = ? WHERE id = ?", (income, costs, biz))
    engine = RuleEngine(conn)
    assert engine.resolve("SAFEWAY #1 SPRINGFIELD", amount=-10, account_id=biz).category_id == costs  # beats the Groceries rule
    assert engine.resolve("CLIENT DEPOSIT", amount=300, account_id=biz).category_id == income
    assert engine.resolve("SAFEWAY #1 SPRINGFIELD", amount=-10, account_id=home).category_id == category(conn, "Groceries")
    assert engine.resolve("PAYMENT THANK YOU", amount=-100, account_id=biz).category_id == category(conn, "Transfer")


def test_hand_picked_category_survives_rules(conn):
    acct = account(conn, "Checking")
    store_transactions(conn, acct, "a.csv", parse_csv(US_BANK))
    tid = conn.execute("SELECT id FROM transactions WHERE name = 'Safeway' LIMIT 1").fetchone()[0]
    conn.execute("UPDATE transactions SET category_id = ?, category_source = 'manual' WHERE id = ?", (category(conn, "Travel"), tid))
    reapply(conn)
    assert conn.execute("SELECT category_id FROM transactions WHERE id = ?", (tid,)).fetchone()[0] == category(conn, "Travel")


def test_category_checkbox_and_filter(demo_conn, client):
    tid = demo_conn.execute("SELECT id FROM transactions WHERE flag = 'check' LIMIT 1").fetchone()[0]
    assert client.post(f"/api/transactions/{tid}", json={"flag": "yes"}).status_code == 200
    home_garden = category(demo_conn, "Home & Garden")
    page = client.get(f"/transactions?flag=yes:{home_garden}").get_data(as_text=True)
    assert "1 transaction" in page and "Rental property" in page
    client.post(f"/api/transactions/{tid}", json={"flag": None})
    assert client.get(f"/transactions?flag=yes:{home_garden}").get_data(as_text=True).count('class="flag-check" checked') == 0
