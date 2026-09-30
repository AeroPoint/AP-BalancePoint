"""A made-up household (Pat and Sam Smith) to try the app without real bank data.

run.py demo builds it in its own folder (demo-data/ by default), never the real data folder, and prints
how to start the app on it. Everything is invented and repeatable (a fixed random seed): about two years
of checking and card spending, paychecks with a 401(k), a mortgage on a loan schedule, a rental, a small
business account, a child's 529, balances, a plan, tax checkboxes, and a few blackjack sessions.
"""
import json
import random
from datetime import date, timedelta

from . import blackjack, budgeting
from .csv_import import store_transactions
from .excel_import import ensure_account, set_balance

SEED = 7

# (bank text, low, high, times a month, account)
EVERYDAY = [
    ("SAFEWAY #1234 SPRINGFIELD CO", 35, 190, 5, "card"),
    ("COSTCO WHSE #0677 SPRINGFIELD CO", 110, 280, 2, "card"),
    ("STARBUCKS STORE 1022", 5, 14, 5, "card"),
    ("CHIPOTLE 2231 SPRINGFIELD CO", 11, 38, 3, "card"),
    ("SHELL OIL 57444 SPRINGFIELD CO", 38, 72, 3, "card"),
    ("AMAZON MKTPLACE PMTS AMZN.COM/BILL WA", 9, 95, 4, "card"),
    ("TARGET 00012345 SPRINGFIELD CO", 18, 140, 2, "card"),
    ("WALGREENS #4411 SPRINGFIELD CO", 8, 60, 1, "card"),
    ("HOME DEPOT #1510 SPRINGFIELD CO", 25, 420, 1, "card"),
    ("DOORDASH*THAI BASIL", 28, 64, 3, "card"),
    ("SQ *MAPLE STREET BAKERY SPRINGFIELD CO", 7, 24, 2, "card"),  # no rule: shows up to categorize
]
BILLS = [  # (bank text, amount, day of month, account)
    ("XCEL ENERGY", None, 12, "checking"),
    ("VERIZON WIRELESS", -94.18, 20, "card"),
    ("NETFLIX.COM", -15.49, 8, "card"),
    ("SPOTIFY USA", -11.99, 3, "card"),
    ("GEICO *AUTO", -168.40, 17, "checking"),
    ("COMCAST XFINITY", -89.99, 22, "checking"),
    ("SUNSHINE KIDS DAYCARE", -1150.00, 1, "checking"),
]
RULES = [  # the demo household's own dictionary entries: (bank text, name, category)
    ("ACME CORP PAYROLL", "Paycheck", "Paycheck"),
    ("LOANCARE MORTGAGE", "Mortgage", "Mortgage & HOA"),
    ("ZELLE FROM TAYLOR RENTER", "Rent from Taylor", "Rental Income"),
    ("TRANSFER TO SAVINGS", "To savings", "Transfer"),
    ("TRANSFER FROM CHECKING", "From checking", "Transfer"),
    ("TRANSFER TO STUDIO", "To the studio", "Transfer"),
    ("TRANSFER FROM JOINT", "From Joint Checking", "Transfer"),
    ("SQUARE INC DEPOSIT", "Square deposit", None),
    ("ADOBE CREATIVE CLOUD", "Adobe", None),
    ("SUNSHINE KIDS DAYCARE", "Sunshine Kids Daycare", "Kids & Education"),
]


def _months(first, last):
    y, m = first.year, first.month
    while (y, m) <= (last.year, last.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _day(y, m, d, today):
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
    day = date(y, m, min(d, last))
    return day if day <= today else None


def build(conn, today=None):
    """Fill an empty database with the demo household. Returns a short summary."""
    today = today or date.today()
    rnd = random.Random(SEED)
    start = date(today.year - 2, today.month, 1)

    acct = {
        "checking": ensure_account(conn, "Joint Checking", "checking"),
        "card": ensure_account(conn, "Rewards Card", "credit"),
        "savings": ensure_account(conn, "Savings", "savings"),
        "brokerage": ensure_account(conn, "Brokerage", "brokerage"),
        "401k": ensure_account(conn, "401(k)", "retirement"),
        "home": ensure_account(conn, "Home", "property"),
        "mortgage": ensure_account(conn, "Mortgage", "loan"),
        "studio": ensure_account(conn, "Pat's Studio", "checking"),
        "529": ensure_account(conn, "Kid's 529", "held"),
    }
    cat = lambda name, kind="expense": (  # noqa: E731
        conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
        or [conn.execute("INSERT INTO categories (name, kind, sort, grp) VALUES (?, ?, 999, ?)",
                         (name, kind, "flexible" if kind == "expense" else None)).lastrowid]
    )[0]
    rule_cats = {"Studio Income": cat("Studio Income", "income"), "Studio Business": cat("Studio Business")}
    for pattern, name, category in RULES:
        conn.execute("INSERT OR IGNORE INTO rules (match_on, pattern, rename_to, category_id, source) VALUES ('raw', ?, ?, ?, 'user')",
                     (pattern, name, cat(category) if category else None))
    # The studio is a business account: everything on it is its income or its costs.
    conn.execute("UPDATE accounts SET default_in_category = ?, default_out_category = ? WHERE id = ?",
                 (rule_cats["Studio Income"], rule_cats["Studio Business"], acct["studio"]))
    conn.execute("UPDATE categories SET flag = 'Rental property' WHERE name = 'Home & Garden'")
    conn.execute("UPDATE categories SET flag = 'Should be FSA/HSA' WHERE name = 'Health & Fitness'")

    rows = {k: [] for k in acct}
    add = lambda k, day, amount, raw, mcc=None: day and rows[k].append(  # noqa: E731
        {"date": day.isoformat(), "amount": round(amount, 2), "raw": raw, "memo": None, "mcc": mcc})
    for y, m in _months(start, today):
        d = lambda n: _day(y, m, n, today)  # noqa: E731
        for payday in (1, 15):
            add("checking", d(payday), 2650 + rnd.choice([0, 0, 0, 180]), "ACME CORP PAYROLL PPD")
        add("checking", d(1), -2150.00, "LOANCARE MORTGAGE PMT")
        add("checking", d(2), 1275.00, "ZELLE FROM TAYLOR RENTER")
        add("checking", d(5), -500.00, "TRANSFER TO SAVINGS")
        add("savings", d(5), 500.00, "TRANSFER FROM CHECKING")
        for raw, amount, n, where in BILLS:
            add(where, d(n), amount if amount else -rnd.uniform(70, 165), raw)
        card_total = 0.0
        for raw, low, high, times, where in EVERYDAY:
            for _ in range(times + rnd.choice([-1, 0, 0, 1]) if times > 1 else times):
                amount = -rnd.uniform(low, high)
                add(where, d(rnd.randint(1, 28)), amount, raw, "5812" if "BAKERY" in raw else None)
                card_total += amount
        # The card is paid off from checking each month.
        paid = round(-card_total * 0.97, 2)
        add("card", d(26), paid, "INTERNET PAYMENT THANK YOU")
        add("checking", d(26), -paid, "PAYMENT TO CREDIT CARD")
        # Pat's studio: client deposits, software, and money moved in from checking to start it.
        for _ in range(2):
            add("studio", d(rnd.randint(3, 27)), rnd.uniform(180, 900), "SQUARE INC DEPOSIT")
        add("studio", d(9), -59.99, "ADOBE CREATIVE CLOUD")
    add("checking", start.replace(day=10), -1500.00, "TRANSFER TO STUDIO")
    add("studio", start.replace(day=10), 1500.00, "TRANSFER FROM JOINT")
    trip = today.replace(day=1) - timedelta(days=120)
    add("card", trip.replace(day=14), -642.30, "UNITED.COM 0162345")  # a big one-off
    add("card", (today.replace(day=1) - timedelta(days=40)).replace(day=6), -185.00, "SPRINGFIELD SMILES DENTAL")

    added = 0
    for k, parsed in rows.items():
        if parsed:
            added += store_transactions(conn, acct[k], "Demo data", parsed)[2]
    conn.execute("UPDATE transactions SET one_off = 1 WHERE raw_description LIKE 'UNITED.COM%'")
    conn.execute("""UPDATE transactions SET flag = 'check' WHERE category_id = (SELECT id FROM categories WHERE name = 'Home & Garden')
                    AND amount < -150""")

    # Balances: where each account started, and month-end values for ones without transactions.
    first_day = start.isoformat()
    for k, amount in (("checking", 4200), ("card", 0), ("savings", 9000), ("studio", 0)):
        set_balance(conn, acct[k], first_day[:7], amount, first_day, source="manual")
    for i, (y, m) in enumerate(_months(start, today)):
        end = min(date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1), today).isoformat()
        set_balance(conn, acct["brokerage"], end[:7], round(31000 * 1.006 ** i + rnd.uniform(-900, 900), 2), end, source="manual")
        set_balance(conn, acct["401k"], end[:7], round(58000 * 1.009 ** i + rnd.uniform(-1500, 1500), 2), end, source="manual")
        set_balance(conn, acct["529"], end[:7], round(14000 * 1.007 ** i, 2), end, source="manual")
        if i % 6 == 0:
            set_balance(conn, acct["home"], end[:7], round(505000 * 1.012 ** i), end, source="manual")
    conn.execute("""INSERT INTO loan_terms (account_id, label, principal, annual_rate, term_months, start_date, counts_from)
                    VALUES (?, 'Home loan', 400000, 3.1, 360, ?, ?)""",
                 (acct["mortgage"], date(today.year - 4, 3, 1).isoformat(), first_day))

    # Paycheck 401(k), the plan, and a flexible-spending target.
    conn.execute("""INSERT INTO paycheck_savings (pattern, account_id, start_date, base_pay, employee_pct, match_rate, match_cap_pct)
                    VALUES ('ACME CORP PAYROLL', ?, ?, 3400, 6, 50, 6)""", (acct["401k"], first_day))
    for key, value in (("plan_income", 6700), ("plan_fixed", 3500), ("flex_target", 1900), ("plan_nonmonthly", 300),
                       ("plan_buffer", 2000), ("plan_account", acct["checking"]),
                       ("plan_backup_account", acct["savings"]), ("plan_backup_account_2", acct["brokerage"])):
        budgeting.set_setting(conn, key, value)
    conn.execute("INSERT INTO plan_items (label, amount, start_month, end_month) VALUES ('Daycare tuition goes up', -450, ?, ?)",
                 ((today.replace(day=1) + timedelta(days=62)).strftime("%Y-%m"), (today.replace(day=1) + timedelta(days=150)).strftime("%Y-%m")))

    # A few blackjack sessions, research for a trip, and practice.
    bankroll = blackjack.bankroll_account(conn)
    set_balance(conn, bankroll, first_day[:7], 1000, first_day, source="manual")
    game = {"17": "H", "DAS": "Y", "RSA": "Y", "BJ": "3:2", "Decks": 6, "Double": "All", "Cutoff": 1, "SP#": 4,
            "# Players": 2, "Spread": "1-12", "Min": 15, "Max": 300}
    for i, (loc, result, hours, ev_hour) in enumerate([
        ("Riverside Casino", 340, 2.5, 38.0), ("Riverside Casino", -520, 3.0, 36.5), ("Canyon Club", 185, 1.5, 44.0),
        ("Canyon Club", -95, 1.0, 41.0), ("Lakeview Resort", 610, 2.75, 52.0),
    ]):
        blackjack.save_session(conn, {
            "date": (start + timedelta(days=70 * i + 20)).isoformat(), "location": loc, "result": result,
            "miles": 60, "notes": "Demo session", "tables": [{"hours": hours, "ev_hour": ev_hour, "rules": game}],
        }, bankroll)
    for casino, fields in (("Lakeview Resort", {"Decks": 2, "Pen": 0.6, "Min": 25, "Max": 500, "EV": 96.2, "17 Rule": "S17"}),
                           ("Canyon Club", {"Decks": 6, "Pen": 1, "Min": 15, "Max": 300, "EV": 41.0, "17 Rule": "H17"})):
        conn.execute("INSERT INTO bj_research (region, casino, fields) VALUES ('Weekend trip', ?, ?)",
                     (casino, json.dumps(fields)))
    conn.execute("""INSERT INTO bj_training (date, minutes, fields) VALUES (?, 30, '{"Source": "Card counting drills", "Count": "Hi-Lo, 6 decks"}')""",
                 ((today - timedelta(days=10)).isoformat(),))
    conn.commit()
    return {"accounts": len(acct) + 1, "transactions": added, "from": first_day}
