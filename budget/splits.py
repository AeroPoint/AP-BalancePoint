"""Splitting one charge across categories (merchants marked "Can be split" in the Merchant dictionary).

You type the amounts you know for each category; what's left of the charge is spread over those lines
in proportion to them, to the cent. A $50 charge with $30 Groceries and $10 Home leaves $10, which
becomes +$7.50 and +$2.50: $37.50 and $12.50. One line may leave its amount blank to take the rest.

Storage keeps every sum right without touching any report: the bank's row stays and holds the first
part (its amount and category), with the bank's total in split_total; each other part is its own row
(split_of = the bank row) on the same account and date. Balances, month totals, business and tax pages
add the parts back up to the charge. The bank row keeps its dedupe key, which is built from the bank's
amount, so a re-uploaded export or a re-sync still recognizes it; the parts' keys are split|<id>|<n>.
"""
import math

from .rules import RuleEngine, sync_dates


class SplitError(ValueError):
    pass


def _cents(value):
    return int(round(abs(value) * 100))


def split_amounts(total, known):
    """Final amounts for a split of `total` (signed, as stored).

    known: one amount per line, positive (the sign follows the charge); None for the one line that
    takes whatever is left. Without such a line, the rest is spread over the lines in proportion to
    their amounts, each rounded to the cent, and the rounding leftover goes to the largest line so the
    parts add up to the charge exactly. Raises SplitError with a message for the page.
    """
    if len(known) < 2:
        raise SplitError("Split into at least two parts.")
    blanks = [i for i, k in enumerate(known) if k is None]
    if len(blanks) > 1:
        raise SplitError("Only one line can leave its amount blank (it takes what's left).")
    if any(k is not None and k <= 0 for k in known):
        raise SplitError("Each amount must be more than zero. Leave one blank to give it what's left.")
    total_c = _cents(total)
    parts = [None if k is None else _cents(k) for k in known]
    if any(p == 0 for p in parts if p is not None):
        raise SplitError("Each amount must be at least a cent.")
    claimed = sum(p for p in parts if p is not None)
    if claimed > total_c:
        raise SplitError(f"The amounts add up to {claimed / 100:,.2f}, more than the charge ({total_c / 100:,.2f}).")
    rest = total_c - claimed
    if blanks:
        if rest == 0:
            raise SplitError("Nothing is left for the line without an amount.")
        parts[blanks[0]] = rest
    elif rest:
        spread = [p * total_c / claimed for p in parts]
        parts = [math.floor(s + 0.5) for s in spread]  # half a cent rounds up, as the page's preview does
        biggest = max(range(len(parts)), key=lambda i: (spread[i], -i))
        parts[biggest] += total_c - sum(parts)
    sign = -1 if total < 0 else 1
    return [sign * p / 100 for p in parts]


def family(conn, tid):
    """(bank row, [other parts]) for a transaction or any of its parts; (None, []) if not found."""
    row = conn.execute("SELECT * FROM transactions WHERE id = ?", (tid,)).fetchone()
    if row is None:
        return None, []
    if row["split_of"] is not None:
        row = conn.execute("SELECT * FROM transactions WHERE id = ?", (row["split_of"],)).fetchone()
    children = conn.execute("SELECT * FROM transactions WHERE split_of = ? ORDER BY id", (row["id"],)).fetchall()
    return row, children


def can_split(conn, row, engine=None):
    """None if this transaction may be split, else why not."""
    if row["pending"]:
        return "A pending charge can be split once it posts."
    if str(row["dedupe_key"]).startswith("bjsession|"):
        return "Blackjack sessions can't be split."
    if row["split_total"] is not None:
        return None  # already split: can always be changed or undone
    engine = engine or RuleEngine(conn)
    if not engine.merchant_flags(row["raw_description"], row["name"], row["amount"]).allow_split:
        return "Turn on “Can be split” for this merchant in the Merchant dictionary first."
    return None


def split(conn, tid, lines):
    """Split a transaction: lines = [(category_id, known amount or None)]. Replaces an earlier split.
    Returns the parts' amounts."""
    parent, children = family(conn, tid)
    if parent is None:
        raise SplitError("Transaction not found.")
    why = can_split(conn, parent)
    if why:
        raise SplitError(why)
    if any(not c for c, _ in lines):
        raise SplitError("Pick a category for each line.")
    total = parent["split_total"] if parent["split_total"] is not None else parent["amount"]
    amounts = split_amounts(total, [k for _, k in lines])
    conn.execute("DELETE FROM transactions WHERE split_of = ?", (parent["id"],))
    (first_cat, _), first_amount = lines[0], amounts[0]
    conn.execute(
        "UPDATE transactions SET amount = ?, split_total = ?, category_id = ?, category_source = 'manual' WHERE id = ?",
        (first_amount, total, first_cat, parent["id"]),
    )
    for n, ((category_id, _), amount) in enumerate(zip(lines[1:], amounts[1:]), start=1):
        conn.execute(
            """INSERT INTO transactions (account_id, import_id, date, date_override, effective_date, amount,
                   raw_description, memo, mcc, name, name_locked, category_id, category_source, notes, one_off,
                   flag, pending, dedupe_key, split_of)
               SELECT account_id, import_id, date, date_override, effective_date, ?, raw_description, memo, mcc,
                   name, name_locked, ?, 'manual', notes, one_off, flag, 0, ?, id
               FROM transactions WHERE id = ?""",
            (amount, category_id, f"split|{parent['id']}|{n}", parent["id"]),
        )
    sync_dates(conn, "id = ? OR split_of = ?", (parent["id"], parent["id"]))
    return amounts


def unsplit(conn, tid):
    """Put a split charge back together on the bank row, keeping the first part's category."""
    parent, _ = family(conn, tid)
    if parent is None or parent["split_total"] is None:
        return False
    conn.execute("DELETE FROM transactions WHERE split_of = ?", (parent["id"],))
    conn.execute("UPDATE transactions SET amount = split_total, split_total = NULL WHERE id = ?", (parent["id"],))
    return True


def labels(conn, ids):
    """{id: {"n", "of", "total", "parent"}} for the split parts among these transaction ids."""
    ids = list(ids)
    if not ids:
        return {}
    marks = ", ".join("?" * len(ids))
    parents = {r[0] for r in conn.execute(
        f"SELECT COALESCE(split_of, id) FROM transactions WHERE id IN ({marks}) AND (split_of IS NOT NULL OR split_total IS NOT NULL)", ids)}
    if not parents:
        return {}
    marks = ", ".join("?" * len(parents))
    rows = conn.execute(
        f"""SELECT id, split_of, split_total FROM transactions WHERE id IN ({marks}) OR split_of IN ({marks})
            ORDER BY COALESCE(split_of, id), split_of IS NOT NULL, id""", (*parents, *parents)).fetchall()
    groups = {}
    for r in rows:
        groups.setdefault(r["split_of"] or r["id"], []).append(r)
    out = {}
    for parent_id, members in groups.items():
        total = members[0]["split_total"]
        for n, r in enumerate(members, start=1):
            out[r["id"]] = {"n": n, "of": len(members), "total": total, "parent": parent_id}
    return out
