"""Merging two accounts moves everything that points at the old one."""
from budget import budgeting
from budget import simplefin_import as sf
from budget.csv_import import store_transactions

from conftest import account, category


def test_merge_carries_links(conn, client):
    old, new = account(conn, "Old Checking"), account(conn, "New Checking")
    store_transactions(conn, old, "a.csv", [{"date": "2026-03-02", "amount": -5.0, "raw": "SAFEWAY #1", "memo": None, "mcc": None}])
    conn.execute("""INSERT INTO transactions (account_id, date, effective_date, amount, raw_description, name, dedupe_key, pending)
                    VALUES (?, '2026-03-30', '2026-03-30', -9.0, 'PENDING THING', 'Pending', ?, 1)""", (old, f"sfpending|{old}|P1"))
    sf.map_account(conn, "ACT-OLD", old, sync_from="2026-03-01")
    conn.execute("INSERT INTO paycheck_savings (pattern, account_id, start_date, base_pay, employee_pct) VALUES ('ACME', ?, '2026-01-01', 1000, 5)", (old,))
    budgeting.set_setting(conn, "plan_account", old)
    budgeting.set_setting(conn, "plan_backup_account", old)
    conn.execute("UPDATE accounts SET default_in_category = ?, default_out_category = ? WHERE id = ?",
                 (category(conn, "Side Income"), category(conn, "Other"), old))
    conn.commit()
    assert client.post(f"/accounts/{old}/merge", data={"target": new}).status_code == 302

    keys = {r[0] for r in conn.execute("SELECT dedupe_key FROM transactions WHERE account_id = ?", (new,))}
    assert f"sfpending|{new}|P1" in keys  # a later sync still recognizes it, so it isn't stored twice
    assert any(k.startswith(f"{new}|2026-03-02|") for k in keys)
    assert conn.execute("SELECT account_id FROM simplefin_accounts WHERE external_id = 'ACT-OLD'").fetchone()[0] == new
    assert conn.execute("SELECT account_id FROM paycheck_savings").fetchone()[0] == new
    assert budgeting.get_setting(conn, "plan_account", cast=int) == new
    assert budgeting.get_setting(conn, "plan_backup_account", cast=int) == new
    assert conn.execute("SELECT default_in_category FROM accounts WHERE id = ?", (new,)).fetchone()[0] == category(conn, "Side Income")
