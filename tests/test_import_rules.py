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
    demo_conn.execute("UPDATE transactions SET flag = NULL WHERE flag = 'yes'")  # start with none ticked
    demo_conn.commit()
    tid = demo_conn.execute("SELECT id FROM transactions WHERE flag = 'check' LIMIT 1").fetchone()[0]
    assert client.post(f"/api/transactions/{tid}", json={"flag": "yes"}).status_code == 200
    home_garden = category(demo_conn, "Home & Garden")
    page = client.get(f"/transactions?flag=yes:{home_garden}").get_data(as_text=True)
    assert "1 transaction" in page and "Rental property" in page
    client.post(f"/api/transactions/{tid}", json={"flag": None})
    assert client.get(f"/transactions?flag=yes:{home_garden}").get_data(as_text=True).count('class="flag-check" checked') == 0


AMEX_STYLE = "Date,Description,Amount\n03/02/2026,ACME HARDWARE,54.21\n03/04/2026,PAYMENT RECEIVED,-500.00\n"


def test_flip_sign_for_banks_that_list_purchases_as_positive():
    assert [r["amount"] for r in parse_csv(AMEX_STYLE)] == [54.21, -500.0]  # default: as the file says
    assert [r["amount"] for r in parse_csv(AMEX_STYLE, flip_sign=True)] == [-54.21, 500.0]
    # Debit/credit columns already say which way money went: never flipped.
    both = "Posted Date,Description,Debit,Credit\n03/04/2026,COFFEE,4.50,\n03/05/2026,REFUND,,20.00\n"
    assert [r["amount"] for r in parse_csv(both, flip_sign=True)] == [-4.5, 20.0]


def test_headerless_export():
    text = '"03/02/2026","-54.21","*","","SAFEWAY #1234 SPRINGFIELD"\n"03/03/2026","2650.00","*","","ACME CORP PAYROLL"\n'
    rows = parse_csv(text)
    assert [(r["date"], r["amount"], r["raw"]) for r in rows] == [
        ("2026-03-02", -54.21, "SAFEWAY #1234 SPRINGFIELD"), ("2026-03-03", 2650.0, "ACME CORP PAYROLL")]


def test_flip_sign_is_remembered_on_the_account(conn, client):
    import io

    acct = account(conn, "Rewards Card", "credit")
    conn.commit()
    flag = lambda: conn.execute("SELECT csv_flip_sign FROM accounts WHERE id = ?", (acct,)).fetchone()[0]  # noqa: E731
    assert flag() == 0  # default off

    client.post("/upload", data={"account_id": acct, "flip_sign_shown": "1", "csv_flip_sign": "1",
                                 "files": (io.BytesIO(AMEX_STYLE.encode()), "jan.csv")},
                content_type="multipart/form-data")
    amounts = sorted(r[0] for r in conn.execute("SELECT amount FROM transactions WHERE account_id = ?", (acct,)))
    assert amounts == [-54.21, 500.0]
    assert flag() == 1
    assert 'data-flip="1"' in client.get("/upload").get_data(as_text=True)
    # A form without the box leaves it alone; the Accounts page row (which has it) unticked turns it off.
    client.post("/accounts/save", data={"id": acct, "name": "Rewards Card", "kind": "credit"})
    assert flag() == 1
    client.post("/accounts/save", data={"id": acct, "name": "Rewards Card", "kind": "credit", "flip_sign_shown": "1"})
    assert flag() == 0


def test_older_database_gets_the_flip_setting_off(conn):
    from budget.db import migrate

    acct = account(conn, "Checking")
    conn.execute("ALTER TABLE accounts DROP COLUMN csv_flip_sign")
    migrate(conn)
    assert conn.execute("SELECT csv_flip_sign FROM accounts WHERE id = ?", (acct,)).fetchone()[0] == 0


REMOVED_BUILTINS = [("raw", "CLOUDFLARE"), ("raw", "MERRILL"), ("raw", "ML "),
                    ("raw", "VOYA"), ("name", "Chase Card"), ("name", "Merrill"), ("name", "Merrill Lynch"),
                    ("name", "Check Deposit"), ("name", "Xmas"), ("name", "Christmas"), ("name", "Student Loans")]


def _rule(conn, match_on, pattern):
    return conn.execute("SELECT rename_to, category_id, source FROM rules WHERE match_on = ? AND pattern = ?",
                        (match_on, pattern)).fetchone()


def test_fresh_database_has_only_national_merchants(conn):
    for match_on, pattern in REMOVED_BUILTINS:
        assert _rule(conn, match_on, pattern) is None, pattern
    assert _rule(conn, "raw", "CARDMEMBER SERV")["rename_to"] == "Card Payment"


def test_existing_database_keeps_rules_dropped_from_the_built_in_list(conn):
    """Taking an entry out of seed.py, or renaming one, never deletes or rewrites what a database already has."""
    from budget.db import init_db

    other = category(conn, "Other Income")
    for match_on, pattern in REMOVED_BUILTINS:
        conn.execute("INSERT INTO rules (match_on, pattern, rename_to, category_id, source) VALUES (?, ?, ?, ?, 'builtin')",
                     (match_on, pattern, pattern.title() if match_on == "raw" else None, other))
    conn.execute("UPDATE rules SET rename_to = 'US Bank Card Payment' WHERE pattern = 'CARDMEMBER SERV'")
    conn.execute("UPDATE rules SET rename_to = 'My Card', source = 'user' WHERE pattern = 'PAYMENT THANK YOU'")
    conn.commit()
    init_db(conn)
    for match_on, pattern in REMOVED_BUILTINS:
        row = _rule(conn, match_on, pattern)
        assert row is not None and row["source"] == "builtin" and row["category_id"] == other, pattern
    assert _rule(conn, "raw", "CARDMEMBER SERV")["rename_to"] == "US Bank Card Payment"
    row = _rule(conn, "raw", "PAYMENT THANK YOU")
    assert (row["rename_to"], row["source"]) == ("My Card", "user")


def test_unreadable_dates_are_counted_not_dropped_quietly(client, conn):
    """A DD/MM file: rows whose date can't be read are reported on the upload page."""
    acct = account(conn, "Checking")
    conn.commit()
    body = "Date,Description,Amount\n25/09/2026,CAFE,-4.50\n03/14/2026,BAKERY,-6.00\n"
    from io import BytesIO
    r = client.post("/upload", data={"account_id": acct, "files": (BytesIO(body.encode()), "dd-mm.csv")},
                    content_type="multipart/form-data", follow_redirects=True)
    html = r.get_data(as_text=True)
    assert "added 1 of 1" in html and "1 row(s) had a date it couldn" in html
    assert parse_csv(body).bad_dates == 1
