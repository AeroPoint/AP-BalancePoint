"""Per-merchant settings in the Merchant dictionary: "Sort by hand" and "Can be split" (made-up data)."""
from datetime import date, timedelta

import pytest

from budget import recurring, splits
from budget import simplefin_import as sf
from budget.balances import load_ledgers
from budget.csv_import import replace_spreadsheet_rows, store_transactions
from budget.rules import RuleEngine, reapply
from budget.views import upsert_rule

from conftest import account, category, txn
from test_simplefin import TODAY, _ts, bridge  # noqa: F401  (the fake Bridge fixture)


def rule_id(conn, pattern):
    return conn.execute("SELECT id FROM rules WHERE match_on = 'raw' AND pattern = ?", (pattern,)).fetchone()[0]


def set_flags(client, conn, pattern, **flags):
    """Tick or clear the checkboxes on one dictionary row and save it, as the page does."""
    r = conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id(conn, pattern),)).fetchone()
    form = {"id": r["id"], "match_on": "raw", "pattern": r["pattern"], "rename_to": r["rename_to"] or "",
            "category_id": r["category_id"] or "", "flags_shown": "1"}
    form.update({k: "1" for k, v in flags.items() if v})
    assert client.post("/rules/save", data=form).status_code == 302


def splittable(conn, pattern, name):
    conn.execute("INSERT OR IGNORE INTO rules (match_on, pattern, source) VALUES ('raw', ?, 'user')", (pattern,))
    conn.execute("UPDATE rules SET rename_to = ?, allow_split = 1 WHERE match_on = 'raw' AND pattern = ? AND amount IS NULL",
                 (name, pattern))
    conn.commit()


def row(conn, tid):
    return conn.execute("SELECT * FROM transactions WHERE id = ?", (tid,)).fetchone()


CSV_ROWS = [
    {"date": "2026-03-02", "amount": -50.00, "raw": "AMZN MKTP US*1A2B3C", "memo": None, "mcc": "5942"},
    {"date": "2026-03-02", "amount": -50.00, "raw": "AMZN MKTP US*1A2B3C", "memo": None, "mcc": "5942"},  # same-day repeat
    {"date": "2026-03-03", "amount": -14.99, "raw": "AMAZON PRIME*9Z8Y7X", "memo": None, "mcc": None},
    {"date": "2026-03-04", "amount": 20.00, "raw": "AMAZON.COM REFUND", "memo": None, "mcc": None},
    {"date": "2026-03-05", "amount": -8.00, "raw": "PAT'S COFFEE", "memo": None, "mcc": None},
]


@pytest.fixture
def amazon(conn, client):
    """A card with some Amazon charges; Amazon set to both "Sort by hand" and "Can be split"."""
    card = account(conn, "Card", "credit")
    store_transactions(conn, card, "card.csv", CSV_ROWS)
    conn.execute("INSERT INTO balances (account_id, month, amount, as_of) VALUES (?, '2026-02', 500, '2026-02-28')", (card,))
    conn.commit()
    set_flags(client, conn, "AMAZON MKTPL", leave_uncategorized=True, allow_split=True)
    ids = {r["raw_description"] + str(i): r["id"] for i, r in enumerate(
        conn.execute("SELECT id, raw_description FROM transactions ORDER BY id"))}
    return card, ids


# ---------------------------------------------------------------- sort by hand

def test_setting_one_entry_sets_the_whole_merchant(conn, client):
    set_flags(client, conn, "AMAZON MKTPL", leave_uncategorized=True)
    flagged = {r[0] for r in conn.execute("SELECT pattern FROM rules WHERE match_on = 'raw' AND leave_uncategorized = 1")}
    assert {"AMAZON MKTPL", "AMZN MKTP", "AMAZON.COM", "AMAZON RETA"} <= flagged
    assert not flagged & {"AMAZON PRIME", "AMZNFREETIME", "AMAZON DIGIT", "AMAZON MKTPLACE PMTS"}
    # Clearing it on any other Amazon entry clears it for the merchant.
    set_flags(client, conn, "AMZN MKTP")
    assert not conn.execute("SELECT 1 FROM rules WHERE leave_uncategorized = 1").fetchone()
    # A form without the checkboxes (the keyword tab, older pages) leaves them alone.
    set_flags(client, conn, "AMAZON MKTPL", allow_split=True)
    client.post("/rules/save", data={"id": rule_id(conn, "AMAZON MKTPL"), "match_on": "raw", "pattern": "AMAZON MKTPL",
                                     "rename_to": "Amazon", "category_id": category(conn, "Shopping & Personal")})
    assert conn.execute("SELECT allow_split FROM rules WHERE pattern = 'AMAZON MKTPL'").fetchone()[0] == 1


def test_sorted_by_hand_beats_every_rule(conn, client):
    engine = RuleEngine(conn)
    assert engine.resolve("AMZN MKTP US*1A2B3C", "5942").category_id == category(conn, "Shopping & Personal")
    set_flags(client, conn, "AMAZON MKTPL", leave_uncategorized=True)
    upsert_rule(conn, "name", "Amazon", category_id=category(conn, "Groceries"))  # "Always put Amazon in Groceries"
    conn.execute("INSERT INTO rules (match_on, pattern, category_id, source) VALUES ('name', 'Amaz', ?, 'config')",
                 (category(conn, "Hobbies & Fun"),))  # a keyword
    engine = RuleEngine(conn)
    m = engine.resolve("AMZN MKTP US*1A2B3C", "5942")  # card type says Shopping too
    assert (m.name, m.category_id, m.source) == ("Amazon", None, "none")
    assert engine.resolve("AMAZON.COM*AB12CD", None, "Amazon").category_id is None  # a name typed by hand
    # Other Amazon merchants keep their own categories.
    for raw, name, cat in [("AMAZON PRIME*9Z8Y7X", "Amazon Prime", "Bills & Utilities"),
                           ("AMZNFREETIME", "Amazon Kids+", "Bills & Utilities"),
                           ("AMAZON DIGIT*AB12CD", "Amazon Digital", "Bills & Utilities"),
                           ("AMAZON MKTPLACE PMTS", "Amazon Refund", "Other Income")]:
        m = engine.resolve(raw)
        assert (m.name, m.category_id) == (name, category(conn, cat)), raw


def test_an_entry_without_a_name_sorts_by_hand_too(conn, client):
    conn.execute("INSERT INTO rules (match_on, pattern, category_id, source, leave_uncategorized) "
                 "VALUES ('raw', 'ACME HARDWARE', ?, 'user', 1)", (category(conn, "Home & Garden"),))
    m = RuleEngine(conn).resolve("ACME HARDWARE #12 SPRINGFIELD", "5200")
    assert m.category_id is None and m.source == "none"


def test_reapply_leaves_hand_picked_categories(conn, client, amazon):
    card, ids = amazon
    reapply(conn)
    conn.commit()
    a1, a2 = ids["AMZN MKTP US*1A2B3C0"], ids["AMZN MKTP US*1A2B3C1"]
    assert row(conn, a1)["category_id"] is None and row(conn, a1)["category_source"] == "none"
    assert row(conn, ids["AMAZON PRIME*9Z8Y7X2"])["category_id"] == category(conn, "Bills & Utilities")
    # Picked by hand: stays, and "Always" isn't learned for a merchant sorted by hand.
    resp = client.post(f"/api/transactions/{a1}", json={"category_id": category(conn, "Groceries"), "remember": True})
    assert resp.status_code == 200
    reapply(conn)
    conn.commit()
    assert (row(conn, a1)["category_id"], row(conn, a1)["category_source"]) == (category(conn, "Groceries"), "manual")
    assert row(conn, a2)["category_id"] is None
    assert not conn.execute("SELECT 1 FROM rules WHERE match_on = 'name' AND pattern = 'Amazon'").fetchone()
    # Categorize lists them one by one, not as one merchant.
    page = client.get("/categorize").get_data(as_text=True)
    assert "Sorted by hand (2)" in page and f'data-id="{a2}"' in page and 'data-merchant="Amazon"' not in page
    # Turning it off files them by the rules again.
    set_flags(client, conn, "AMAZON MKTPL")
    assert row(conn, a2)["category_id"] == category(conn, "Shopping & Personal")
    assert row(conn, a1)["category_id"] == category(conn, "Groceries")


# ---------------------------------------------------------------- split math

def test_split_amounts():
    assert splits.split_amounts(-50.00, [30, 10]) == [-37.50, -12.50]  # $10 left: +7.50 and +2.50
    assert splits.split_amounts(-50.00, [30, None]) == [-30.00, -20.00]  # a blank line takes the rest
    assert splits.split_amounts(-50.00, [30, 20]) == [-30.00, -20.00]
    parts = splits.split_amounts(-10.00, [1, 1, 1])
    assert sorted(parts) == [-3.34, -3.33, -3.33] and round(sum(parts), 2) == -10.00 and parts[0] == -3.34
    parts = splits.split_amounts(-100.01, [33.33, 50, 0.01])
    assert round(sum(parts), 2) == -100.01 and all(round(p, 2) == p for p in parts)
    assert splits.split_amounts(50.00, [30, 10]) == [37.50, 12.50]  # a refund keeps its sign
    for known, message in [([30], "at least two"), ([60, 10], "more than the charge"), ([30, 0], "more than zero"),
                           ([30, -5], "more than zero"), ([None, None], "Only one"), ([50, None], "Nothing is left")]:
        with pytest.raises(splits.SplitError, match=message):
            splits.split_amounts(-50.00, known)


# ---------------------------------------------------------------- storage

def category_totals(conn, account_id):
    return dict(conn.execute("SELECT COALESCE(category_id, 0), ROUND(SUM(amount), 2) FROM transactions "
                             "WHERE account_id = ? GROUP BY 1", (account_id,)).fetchall())


def test_split_keeps_every_total(conn, client, amazon):
    card, ids = amazon
    a1 = ids["AMZN MKTP US*1A2B3C0"]
    today = date.today().isoformat()
    before_balance = load_ledgers(conn)[card].on(today)
    assert before_balance is not None
    before_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    key = row(conn, a1)["dedupe_key"]
    groc, home = category(conn, "Groceries"), category(conn, "Home & Garden")

    resp = client.post(f"/api/transactions/{a1}/split", json={"lines": [
        {"category_id": groc, "amount": "30"}, {"category_id": home, "amount": "10.00"}]})
    assert resp.status_code == 200 and resp.get_json()["amounts"] == [-37.5, -12.5]
    parent = row(conn, a1)
    child = conn.execute("SELECT * FROM transactions WHERE split_of = ?", (a1,)).fetchone()
    assert (parent["amount"], parent["split_total"], parent["category_id"], parent["category_source"]) == (-37.5, -50.0, groc, "manual")
    assert parent["dedupe_key"] == key  # still the bank's key
    assert (child["amount"], child["category_id"], child["dedupe_key"], child["account_id"], child["date"]) == \
        (-12.5, home, f"split|{a1}|1", card, "2026-03-02")
    assert load_ledgers(conn)[card].on(today) == before_balance
    totals = category_totals(conn, card)
    assert totals[groc] == -37.5 and totals[home] == -12.5

    # Re-uploading the same export adds nothing (the same-day repeat is still told apart).
    _, _, added, _, _ = store_transactions(conn, card, "card.csv", CSV_ROWS)
    assert added == 0
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == before_count + 1
    # Rules leave the parts alone.
    reapply(conn)
    conn.commit()
    assert row(conn, a1)["category_id"] == groc

    # The Transactions page labels the parts and offers to change the split.
    page = client.get("/transactions").get_data(as_text=True)
    assert "Split 1 of 2 · from $50.00" in page and "Split 2 of 2 · from $50.00" in page and "Edit split" in page

    # Splitting again replaces the parts; unsplitting puts the charge back.
    resp = client.post(f"/api/transactions/{child['id']}/split", json={"lines": [
        {"category_id": groc, "amount": "10"}, {"category_id": home, "amount": "10"}, {"category_id": groc, "amount": ""}]})
    assert resp.status_code == 200
    assert [r[0] for r in conn.execute("SELECT amount FROM transactions WHERE id = ? OR split_of = ? ORDER BY id", (a1, a1))] == [-10, -10, -30]
    assert load_ledgers(conn)[card].on(today) == before_balance
    assert client.post(f"/api/transactions/{a1}/unsplit").status_code == 200
    assert (row(conn, a1)["amount"], row(conn, a1)["split_total"]) == (-50.0, None)
    assert not conn.execute("SELECT 1 FROM transactions WHERE split_of IS NOT NULL").fetchone()
    assert load_ledgers(conn)[card].on(today) == before_balance


def test_refund_split_and_errors(conn, client, amazon):
    card, ids = amazon
    refund = ids["AMAZON.COM REFUND3"]
    groc, home = category(conn, "Groceries"), category(conn, "Home & Garden")
    assert client.post(f"/api/transactions/{refund}/split", json={"lines": [
        {"category_id": groc, "amount": "15"}, {"category_id": home, "amount": "1"}]}).status_code == 200
    assert sorted(r[0] for r in conn.execute("SELECT amount FROM transactions WHERE id = ? OR split_of = ?", (refund, refund))) == [1.25, 18.75]
    a1 = ids["AMZN MKTP US*1A2B3C0"]
    for lines, message in [([{"category_id": groc, "amount": "45"}, {"category_id": home, "amount": "10"}], "more than the charge"),
                           ([{"category_id": groc, "amount": "0"}, {"category_id": home, "amount": "10"}], "more than zero"),
                           ([{"category_id": groc, "amount": "5"}, {"category_id": "", "amount": "10"}], "Pick a category"),
                           ([{"category_id": groc, "amount": "abc"}, {"category_id": home, "amount": "10"}], "like 12.50")]:
        resp = client.post(f"/api/transactions/{a1}/split", json={"lines": lines})
        assert resp.status_code == 400 and message in resp.get_json()["error"]
    assert row(conn, a1)["amount"] == -50.0 and row(conn, a1)["split_total"] is None


def test_split_only_for_merchants_that_allow_it(conn, client, amazon):
    card, ids = amazon
    coffee, prime = ids["PAT'S COFFEE4"], ids["AMAZON PRIME*9Z8Y7X2"]
    page = client.get("/transactions").get_data(as_text=True)
    assert page.count('class="btn small quiet split-open"') == 3  # the two Amazon charges and the refund
    lines = [{"category_id": category(conn, "Groceries"), "amount": "1"}, {"category_id": category(conn, "Other"), "amount": "1"}]
    for tid in (coffee, prime):
        resp = client.post(f"/api/transactions/{tid}/split", json={"lines": lines})
        assert resp.status_code == 400 and "Can be split" in resp.get_json()["error"]
    pending = txn(conn, card, "2026-03-06", -40.0, name="Amazon", pending=1)
    conn.commit()
    resp = client.post(f"/api/transactions/{pending}/split", json={"lines": lines})
    assert resp.status_code == 400 and "pending" in resp.get_json()["error"]


def test_delete_a_split_hand_entry(conn, client):
    cash = account(conn, "Cash", "cash")
    splittable(conn, "FARMERS MARKET", "Farmers Market")
    conn.commit()
    assert client.post("/transactions/add", data={"account_id": cash, "date": "2026-03-07", "name": "Farmers market",
                                                   "direction": "out", "amount": "40"}).status_code == 302
    tid = conn.execute("SELECT id FROM transactions WHERE account_id = ?", (cash,)).fetchone()[0]
    groc, home = category(conn, "Groceries"), category(conn, "Home & Garden")
    assert client.post(f"/api/transactions/{tid}/split", json={"lines": [
        {"category_id": groc, "amount": "30"}, {"category_id": home, "amount": "10"}]}).status_code == 200
    child = conn.execute("SELECT id FROM transactions WHERE split_of = ?", (tid,)).fetchone()[0]
    client.post(f"/transactions/{child}/delete")  # a part alone can't go
    assert row(conn, child) is not None
    client.post(f"/transactions/{tid}/delete")
    assert not conn.execute("SELECT 1 FROM transactions WHERE account_id = ?", (cash,)).fetchone()


def test_merge_accounts_with_a_split(conn, client, amazon):
    card, ids = amazon
    other = account(conn, "Old Card", "credit")
    store_transactions(conn, other, "old.csv", [CSV_ROWS[0]])
    conn.commit()
    old_charge = conn.execute("SELECT id FROM transactions WHERE account_id = ?", (other,)).fetchone()[0]
    a1 = ids["AMZN MKTP US*1A2B3C0"]
    groc, home = category(conn, "Groceries"), category(conn, "Home & Garden")
    for tid in (a1, old_charge):
        assert client.post(f"/api/transactions/{tid}/split", json={"lines": [
            {"category_id": groc, "amount": "30"}, {"category_id": home, "amount": "10"}]}).status_code == 200
    card_sum = lambda: conn.execute("SELECT ROUND(SUM(amount), 2) FROM transactions WHERE account_id = ?", (card,)).fetchone()[0]  # noqa: E731
    before = card_sum()

    # The old card's charge is the same bank row as one already on the card: it's dropped, parts and all.
    assert client.post(f"/accounts/{other}/merge", data={"target": card}).status_code == 302
    assert row(conn, old_charge) is None
    assert not conn.execute("SELECT 1 FROM transactions WHERE split_of = ?", (old_charge,)).fetchone()
    assert card_sum() == before  # the duplicate is gone with all its parts
    # The kept charge's parts are intact and on the card.
    assert conn.execute("SELECT COUNT(*), SUM(amount) FROM transactions WHERE (id = ? OR split_of = ?) AND account_id = ?",
                        (a1, a1, card)).fetchone()[:] == (2, -50.0)
    _, _, added, _, _ = store_transactions(conn, card, "card.csv", CSV_ROWS)
    assert added == 0


def test_merge_moves_split_parts(conn, client):
    old, new = account(conn, "Old Checking"), account(conn, "New Checking")
    splittable(conn, "COSTCO WHSE", "Costco")
    store_transactions(conn, old, "a.csv", [{"date": "2026-03-02", "amount": -90.0, "raw": "COSTCO WHSE #0123", "memo": None, "mcc": None}])
    tid = conn.execute("SELECT id FROM transactions").fetchone()[0]
    conn.commit()
    assert client.post(f"/api/transactions/{tid}/split", json={"lines": [
        {"category_id": category(conn, "Groceries"), "amount": "60"}, {"category_id": category(conn, "Home & Garden"), "amount": "30"}]}).status_code == 200
    assert client.post(f"/accounts/{old}/merge", data={"target": new}).status_code == 302
    rows = conn.execute("SELECT account_id, amount, dedupe_key FROM transactions ORDER BY id").fetchall()
    assert [r["account_id"] for r in rows] == [new, new] and sum(r["amount"] for r in rows) == -90.0
    assert rows[0]["dedupe_key"].startswith(f"{new}|") and rows[1]["dedupe_key"] == f"split|{tid}|1"
    _, _, added, _, _ = store_transactions(conn, new, "a.csv", [{"date": "2026-03-02", "amount": -90.0, "raw": "COSTCO WHSE #0123", "memo": None, "mcc": None}])
    assert added == 0


def test_resync_after_a_split(bridge, conn, client):  # noqa: F811
    card = account(conn, "Card", "credit")
    sf.map_account(conn, "ACT-CARD", card, sync_from=(TODAY - timedelta(days=5)).isoformat())
    splittable(conn, "SHELL OIL", "Shell")
    conn.commit()
    bridge.accounts = [bridge.accounts[1]]
    sf.sync(conn, bridge.url, TODAY)
    conn.commit()
    tid = conn.execute("SELECT id FROM transactions WHERE account_id = ?", (card,)).fetchone()[0]
    balance = load_ledgers(conn)[card].on(TODAY.isoformat())
    assert client.post(f"/api/transactions/{tid}/split", json={"lines": [
        {"category_id": category(conn, "Auto & Gas"), "amount": "20"}, {"category_id": category(conn, "Groceries"), "amount": "10"}]}).status_code == 200
    r = sf.sync(conn, bridge.url, TODAY)
    conn.commit()
    assert r["accounts"][0]["added"] == 0
    assert conn.execute("SELECT COUNT(*), SUM(amount) FROM transactions WHERE account_id = ?", (card,)).fetchone()[:] == (2, -30.0)
    assert load_ledgers(conn)[card].on(TODAY.isoformat()) == balance


def test_posted_late_moves_every_part(conn, client):
    """A split charge bought on the last day and posted on the 1st counts in the month it happened, parts and all."""
    first = date(2026, 4, 1)
    chk = account(conn, "Checking")
    splittable(conn, "TARGET", "Target")
    store_transactions(conn, chk, "sync", [{"date": first.isoformat(), "amount": -40.0, "raw": "TARGET 00012", "memo": None, "mcc": None}])
    tid = conn.execute("SELECT id FROM transactions").fetchone()[0]
    conn.commit()
    client.post(f"/api/transactions/{tid}/split", json={"lines": [
        {"category_id": category(conn, "Groceries"), "amount": "25"}, {"category_id": category(conn, "Shopping & Personal"), "amount": "15"}]})
    moved = sf.count_in_month_happened(conn, chk, [{"posted": _ts(first, 3), "transacted_at": _ts(first - timedelta(days=1)),
                                                    "amount": "-40.00", "description": "TARGET 00012", "pending": False}], None)
    assert moved == 1
    assert {r[0] for r in conn.execute("SELECT effective_date FROM transactions")} == {"2026-03-31"}


def test_recurring_counts_a_split_charge_whole(conn, client):
    chk = account(conn, "Checking")
    splittable(conn, "ACME GYM", "Acme Gym")
    today = date(2026, 6, 20)
    rows = [{"date": f"2026-0{m}-05", "amount": -60.0, "raw": "ACME GYM", "memo": None, "mcc": None} for m in range(1, 7)]
    store_transactions(conn, chk, "gym.csv", rows)
    conn.commit()
    last = conn.execute("SELECT id FROM transactions ORDER BY date DESC").fetchone()[0]
    client.post(f"/api/transactions/{last}/split", json={"lines": [
        {"category_id": category(conn, "Health & Fitness"), "amount": "40"}, {"category_id": category(conn, "Kids & Education"), "amount": "20"}]})
    gym = next(i for i in recurring.find(conn, today) if i["name"] == "Acme Gym")
    assert gym["cadence"] == "monthly" and gym["typical"] == 60.0 and gym["count"] == 6


def test_bank_export_replacing_spreadsheet_rows_pairs_a_split_charge_whole(conn, client):
    chk = account(conn, "Checking")
    splittable(conn, "COSTCO WHSE", "Costco")
    bank_row = {"date": "2026-03-02", "amount": -90.0, "raw": "COSTCO WHSE #0123", "memo": None, "mcc": None}
    store_transactions(conn, chk, "sheet", [{"date": "2026-03-01", "amount": -90.0, "raw": "Costco run", "memo": None, "mcc": None}],
                       kind="excel")
    store_transactions(conn, chk, "export.csv", [bank_row])
    tid = conn.execute("SELECT id FROM transactions WHERE raw_description = 'COSTCO WHSE #0123'").fetchone()[0]
    conn.commit()
    assert client.post(f"/api/transactions/{tid}/split", json={"lines": [
        {"category_id": category(conn, "Groceries"), "amount": "60"},
        {"category_id": category(conn, "Home & Garden"), "amount": "30"}]}).status_code == 200
    removed, _ = replace_spreadsheet_rows(conn, chk, [bank_row])
    assert removed == 1
    assert conn.execute("SELECT replaced_by FROM replaced_rows").fetchone()[0] == row(conn, tid)["dedupe_key"]
    assert conn.execute("SELECT COUNT(*), SUM(amount) FROM transactions").fetchone()[:] == (2, -90.0)
    assert row(conn, tid)["category_id"] == category(conn, "Groceries")  # the split's categories stand
