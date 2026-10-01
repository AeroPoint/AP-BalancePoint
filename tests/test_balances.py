"""Balances on any day: entered anchors plus transactions, cards and loans, opened/closed months, synced loans."""
from datetime import date, timedelta

import pytest

from budget.balances import (SYNCED_FRESH_DAYS, AccountLedger, load_ledgers, loan_balance, loan_payment, month_end,
                             month_range, months_elapsed)

from conftest import account, txn


def balance(conn, account_id, month, amount, as_of=None, source="manual"):
    conn.execute("INSERT INTO balances (account_id, month, amount, as_of, source) VALUES (?, ?, ?, ?, ?)",
                 (account_id, month, amount, as_of or month_end(month), source))


def on(conn, account_id, day):
    return load_ledgers(conn)[account_id].on(day)


def test_month_helpers():
    assert month_end("2024-02") == "2024-02-29" and month_end("2026-02") == "2026-02-28" and month_end("2026-12") == "2026-12-31"
    assert month_range("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]
    assert months_elapsed("2026-01-31", "2026-02-28") == 0  # like DATEDIF: not a whole month yet
    assert months_elapsed("2026-01-15", "2026-02-15") == 1 and months_elapsed("2025-01-15", "2026-01-14") == 11


def test_loan_math_matches_amortization_tables():
    assert round(loan_payment(200_000, 6, 360), 2) == 1199.10
    assert round(loan_payment(300_000, 3.5, 180), 2) == 2144.65
    assert round(loan_balance(200_000, 6, 360, "2020-01-15", "2021-01-15"), 2) == 197_543.98
    assert loan_balance(200_000, 6, 360, "2020-01-15", "2020-01-15") == 200_000
    assert abs(loan_balance(200_000, 6, 360, "2020-01-15", "2050-01-15")) < 0.01  # paid off
    assert abs(loan_balance(200_000, 6, 360, "2020-01-15", "2070-01-15")) < 0.01  # and stays paid off
    assert loan_payment(12_000, 0, 24) == 500 and loan_balance(12_000, 0, 24, "2026-01-01", "2026-07-01") == 9_000


def test_checking_balance_moves_with_transactions(conn):
    acct = account(conn, "Checking")
    balance(conn, acct, "2026-02", 1000.00, "2026-02-28")
    txn(conn, acct, "2026-02-27", -999.0)  # before the balance: already in it
    txn(conn, acct, "2026-02-28", -999.0)  # same day as the balance: already in it too
    txn(conn, acct, "2026-03-02", -150.25)
    txn(conn, acct, "2026-03-05", 2000.00)
    assert on(conn, acct, "2026-01-31") is None  # nothing known yet
    assert on(conn, acct, "2026-02-28") == 1000.00
    assert on(conn, acct, "2026-03-03") == 849.75
    assert on(conn, acct, "2026-03-31") == 2849.75
    # A newer balance snaps the estimate back.
    balance(conn, acct, "2026-03", 2800.00, "2026-03-10")
    assert on(conn, acct, "2026-03-31") == 2800.00
    ledger = load_ledgers(conn)[acct]
    assert ledger.since_anchor("2026-03-09") == (("2026-02-28", 1000.0), 2)


def test_card_balance_is_what_is_owed(conn):
    card = account(conn, "Card", "credit")
    balance(conn, card, "2026-02", 300.00)
    txn(conn, card, "2026-03-04", -75.00)   # a charge grows the debt
    txn(conn, card, "2026-03-20", 300.00)   # a payment shrinks it
    assert on(conn, card, "2026-03-10") == 375.00
    assert on(conn, card, "2026-03-31") == 75.00


def test_accounts_without_transactions_carry_forward(conn):
    retirement = account(conn, "401k", "retirement")
    balance(conn, retirement, "2025-06", 50_000)
    balance(conn, retirement, "2025-12", 58_000, "2025-12-15")
    assert on(conn, retirement, "2025-09-01") == 50_000
    assert on(conn, retirement, "2025-12-14") == 50_000
    assert on(conn, retirement, "2026-08-01") == 58_000


def test_opened_and_closed_months(conn):
    acct = account(conn, "Old Savings", "savings")
    conn.execute("UPDATE accounts SET opened = '2025-03', closed = '2025-10' WHERE id = ?", (acct,))
    balance(conn, acct, "2025-01", 500)  # entered before it opened on paper
    assert on(conn, acct, "2025-02-28") is None
    assert on(conn, acct, "2025-03-01") == 500
    assert on(conn, acct, "2025-10-31") == 500  # still counts in the month it closed
    assert on(conn, acct, "2025-11-01") == 0.0


def loan(conn, account_id, principal, rate, months, start, counts_from=None):
    conn.execute(
        "INSERT INTO loan_terms (account_id, principal, annual_rate, term_months, start_date, counts_from) VALUES (?, ?, ?, ?, ?, ?)",
        (account_id, principal, rate, months, start, counts_from),
    )


def test_loan_schedule_sets_the_balance_from_counts_from(conn):
    mortgage = account(conn, "Mortgage", "loan")
    balance(conn, mortgage, "2020-06", 199_000)  # typed in before the schedule took over
    loan(conn, mortgage, 200_000, 6, 360, "2020-01-15", counts_from="2020-07-01")
    loan(conn, mortgage, 12_000, 0, 24, "2021-01-01")  # a second loan on the same account, from its start
    assert on(conn, mortgage, "2020-06-30") == 199_000
    assert on(conn, mortgage, "2021-01-15") == 197_543.98 + 12_000
    assert on(conn, mortgage, "2021-07-15") == round(loan_balance(200_000, 6, 360, "2020-01-15", "2021-07-15") + 9_000, 2)
    assert round(load_ledgers(conn)[mortgage].monthly_payment("2021-02-01"), 2) == round(1199.10 + 500, 2)


def test_synced_balance_wins_while_fresh(conn):
    mortgage = account(conn, "Mortgage", "loan")
    loan(conn, mortgage, 200_000, 6, 360, "2020-01-15")
    balance(conn, mortgage, "2021-01", 197_000.00, "2021-01-20", source="simplefin")
    ledger = load_ledgers(conn)[mortgage]
    assert ledger.on("2021-01-19") == round(loan_balance(200_000, 6, 360, "2020-01-15", "2021-01-19"), 2)
    assert ledger.on("2021-01-20") == 197_000.00
    fresh_until = (date(2021, 1, 20) + timedelta(days=SYNCED_FRESH_DAYS)).isoformat()
    assert ledger.on(fresh_until) == 197_000.00 and not ledger.calculated_loans(fresh_until)
    stale = (date(2021, 1, 20) + timedelta(days=SYNCED_FRESH_DAYS + 1)).isoformat()
    assert ledger.on(stale) == round(loan_balance(200_000, 6, 360, "2020-01-15", stale), 2)


def test_a_manual_balance_does_not_replace_the_schedule(conn):
    mortgage = account(conn, "Mortgage", "loan")
    loan(conn, mortgage, 200_000, 6, 360, "2020-01-15")
    balance(conn, mortgage, "2021-01", 1.00, "2021-01-20")
    assert on(conn, mortgage, "2021-01-25") == round(loan_balance(200_000, 6, 360, "2020-01-15", "2021-01-25"), 2)


@pytest.mark.parametrize("kind, expected", [("checking", 90.0), ("credit", 110.0)])
def test_ledger_directly(kind, expected):
    ledger = AccountLedger({"kind": kind, "opened": None, "closed": None}, [("2026-01-31", 100.0)],
                           [("2026-02-02", -10.0, 1)])
    assert ledger.on("2026-02-02") == expected and ledger.has_transactions
