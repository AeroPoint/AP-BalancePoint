"""Parse bank CSV exports (US Bank first, generic Date/Description/Amount second) and store them."""
import csv
import io
import re
from collections import Counter
from datetime import datetime, timedelta

from .rules import RuleEngine, normalize, sync_dates

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%m-%d-%Y")
DATE_COLS = ("date", "transaction date", "posted date", "post date", "posting date", "trans. date")
DESC_COLS = ("name", "description", "payee", "merchant", "original description", "transaction description")
AMOUNT_COLS = ("amount", "transaction amount")
DEBIT_COLS = ("debit", "withdrawal", "withdrawals", "debit amount")
CREDIT_COLS = ("credit", "deposit", "deposits", "credit amount")


class CsvFormatError(ValueError):
    pass


def parse_date(value):
    value = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_amount(value):
    s = str(value or "").strip().replace("$", "").replace(",", "")
    if not s:
        return None
    negative = s.startswith("(") and s.endswith(")")
    try:
        amount = float(s.strip("()"))
    except ValueError:
        return None
    return -amount if negative else amount


def mcc_from_memo(memo):
    """US Bank card memos look like '2404...; 05411; ; ; ;' - the 2nd field is the MCC."""
    parts = [p.strip() for p in str(memo or "").split(";")]
    if len(parts) > 1 and re.fullmatch(r"\d{4,5}", parts[1]):
        return str(int(parts[1])).zfill(4)
    return None


def _find(header, names):
    for i, h in enumerate(header):
        if h in names:
            return i
    return None


def parse_csv(text):
    """Return a list of {date, amount, raw, memo, mcc} dicts."""
    rows = [r for r in csv.reader(io.StringIO(text.lstrip("﻿"))) if any(c.strip() for c in r)]
    for h_idx, row in enumerate(rows[:15]):
        header = [c.strip().lower() for c in row]
        date_i, desc_i = _find(header, DATE_COLS), _find(header, DESC_COLS)
        amount_i = _find(header, AMOUNT_COLS)
        debit_i, credit_i = _find(header, DEBIT_COLS), _find(header, CREDIT_COLS)
        if date_i is not None and desc_i is not None and (amount_i is not None or debit_i is not None):
            break
    else:
        raise CsvFormatError("Couldn't find Date / Name (or Description) / Amount columns in this file.")
    memo_i = _find(header, ("memo", "notes"))

    out = []
    for row in rows[h_idx + 1:]:
        cell = lambda i: row[i] if i is not None and i < len(row) else ""
        date = parse_date(cell(date_i))
        if not date:
            continue
        if amount_i is not None:
            amount = parse_amount(cell(amount_i))
        else:
            amount = -(parse_amount(cell(debit_i)) or 0) + (parse_amount(cell(credit_i)) or 0)
        if amount is None:
            continue
        memo = cell(memo_i).strip()
        out.append({
            "date": date,
            "amount": round(amount, 2),
            "raw": cell(desc_i).strip(),
            "memo": memo,
            "mcc": mcc_from_memo(memo),
        })
    return out


def bank_from(conn, account_id):
    """1st of the month an account's bank exports take over from, or None for any month."""
    row = conn.execute("SELECT bank_from FROM accounts WHERE id = ?", (account_id,)).fetchone()
    return f"{row[0]}-01" if row and row[0] else None


def bank_start(since, first_date):
    """First day a bank export is the record for: the 1st of its first month, but not before the
    account's bank_from month (older months stay as they were)."""
    return max(first_date[:8] + "01", since or "")


def store_transactions(conn, account_id, filename, parsed, kind="csv", engine=None, respect_bank_from=True):
    """Insert parsed rows, skipping ones already imported.

    A row may carry "category_hint": (category_id, compatible category ids or None) from a
    hand-sorted source like the old spreadsheet. When the rules' category doesn't fit the hint
    (None means the hint only fills in uncategorized rows), the hint wins and is stored as
    'sheet', which later rule edits leave alone. Already-imported rows get the hint too.

    Bank rows from before the account's bank_from month are skipped: the history for those months
    was already checked against the bank. Spreadsheet rows a bank row already replaced (see
    replace_spreadsheet_rows) are skipped too.

    Returns (import_id, rows_read, rows_added, rows_categorized_by_hint, rows_before_bank_from).
    """
    engine = engine or RuleEngine(conn)
    since = bank_from(conn, account_id) if kind == "csv" and respect_bank_from else None
    replaced = {r[0] for r in conn.execute("SELECT dedupe_key FROM replaced_rows")} if kind == "excel" else set()
    cur = conn.execute(
        "INSERT INTO imports (filename, account_id, kind, rows_read) VALUES (?, ?, ?, ?)",
        (filename, account_id, kind, len(parsed)),
    )
    import_id = cur.lastrowid
    seen = Counter()
    added = hinted = skipped = 0
    for t in parsed:
        if since and t["date"] < since:
            skipped += 1
            continue
        base = f"{account_id}|{t['date']}|{t['amount']:.2f}|{normalize(t['raw'])}"
        # The Nth identical charge on the same day gets its own key, so re-uploading an
        # overlapping export skips duplicates without dropping real repeat purchases.
        key = f"{base}|{seen[base]}"
        seen[base] += 1
        if key in replaced:
            continue
        fixed_name = t.get("name")
        m = engine.resolve(t["raw"], t.get("mcc"), fixed_name, t["amount"])
        category_id, source = m.category_id, m.source
        if t.get("category_hint"):
            wanted, compatible = t["category_hint"]
            fits = m.category_id is not None and (compatible is None or m.category_id in compatible)
            if not fits:
                category_id, source = wanted, "sheet"
        cur = conn.execute(
            """INSERT OR IGNORE INTO transactions
               (account_id, import_id, date, effective_date, amount, raw_description, memo, mcc,
                name, name_locked, category_id, category_source, dedupe_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, import_id, t["date"], t["date"], t["amount"], t["raw"], t.get("memo"), t.get("mcc"),
             m.name, 1 if fixed_name else 0, category_id, source, key),
        )
        if cur.rowcount:
            added += 1
            hinted += source == "sheet"
        elif source == "sheet":
            hinted += conn.execute(
                "UPDATE transactions SET category_id = ?, category_source = 'sheet' "
                "WHERE dedupe_key = ? AND category_source NOT IN ('manual', 'sheet')",
                (category_id, key),
            ).rowcount
    sync_dates(conn, "account_id = ?", (account_id,))
    conn.execute("UPDATE imports SET rows_added = ? WHERE id = ?", (added, import_id))
    if added == 0:
        conn.execute("DELETE FROM imports WHERE id = ?", (import_id,))
    return import_id, len(parsed), added, hinted, skipped


def replace_spreadsheet_rows(conn, account_id, parsed, filename="bank export"):
    """A bank export is the record for the dates it covers.

    Removes rows and month-end balances imported from an old spreadsheet for the same account, from
    the export's start (see bank_start) through its last transaction. Spreadsheets often hold planned
    bills or entries typed before the bank posted them, so keeping both would double count.

    Spreadsheet dates are loose, so each spreadsheet row is paired with the bank row for the same
    amount nearest in date (within CARRY_DAYS), and what you decided about it moves to the bank row:
    its category, a date you set, notes. A pair that straddles the start is one transaction on both
    sides of the switch, so it's kept once, on the bank's date: a bank row from just before the start
    is brought in, or a spreadsheet row from just before it is dropped.
    Returns (spreadsheet rows removed, bank rows that took something from them).
    """
    since = bank_from(conn, account_id)
    dates = sorted(t["date"] for t in parsed if not since or t["date"] >= since)
    if not dates:
        return 0, 0
    start, last = bank_start(since, dates[0]), dates[-1]
    edge = _shift(start, -CARRY_DAYS)
    conn.execute(
        "DELETE FROM balances WHERE account_id = ? AND source = 'excel' AND as_of >= ?", (account_id, start)
    )
    select = (
        "SELECT t.* FROM transactions t JOIN imports i ON i.id = t.import_id "
        "WHERE t.account_id = ? AND t.date >= ? AND t.date <= ? AND i.kind = ? ORDER BY t.date"
    )
    sheet = conn.execute(select, (account_id, edge, last, "excel")).fetchall()
    bank = [dict(r) for r in conn.execute(select, (account_id, start, last, "csv"))]
    # Bank rows from just before the start aren't stored (that month's record is the spreadsheet),
    # unless an earlier upload brought one in for a straddling pair.
    for t in parsed:
        if edge <= t["date"] < start:
            stored = _stored(conn, account_id, t)
            bank.append(dict(stored) if stored else {"id": None, "date": t["date"], "amount": t["amount"], "parsed": t})

    paired = {r[0] for r in conn.execute("SELECT replaced_by FROM replaced_rows")}
    bank = [b for b in bank if b.get("dedupe_key") not in paired]
    categories = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT id, name, kind FROM categories")}
    carried, dropped = 0, []
    for s, b in _pairs(sheet, bank):
        if s["date"] < start and b["date"] < start:
            continue  # both from before the switch: the spreadsheet stays the record
        if b["id"] is None:
            store_transactions(conn, account_id, f"{filename} (month edge)", [b["parsed"]], respect_bank_from=False)
            b = dict(_stored(conn, account_id, b["parsed"]))
        if s["date"] < start:
            dropped.append(s["id"])
        conn.execute(
            "INSERT OR REPLACE INTO replaced_rows (dedupe_key, replaced_by) VALUES (?, ?)", (s["dedupe_key"], b["dedupe_key"])
        )
        carried += _carry(conn, s, b, categories)
    for sid in dropped:
        conn.execute("DELETE FROM transactions WHERE id = ?", (sid,))
    removed = len(dropped) + conn.execute(
        "DELETE FROM transactions WHERE account_id = ? AND date >= ? AND date <= ? "
        "AND import_id IN (SELECT id FROM imports WHERE kind = 'excel')",
        (account_id, start, last),
    ).rowcount
    sync_dates(conn, "account_id = ?", (account_id,))
    return removed, carried


CARRY_DAYS = 10


def _shift(day, days):
    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")


def _stored(conn, account_id, t):
    return conn.execute(
        "SELECT * FROM transactions WHERE account_id = ? AND date = ? AND ROUND(amount, 2) = ? AND raw_description = ?",
        (account_id, t["date"], round(t["amount"], 2), t["raw"]),
    ).fetchone()


def _pairs(sheet_rows, bank_rows):
    """One-to-one (spreadsheet row, bank row) pairs with the same amount, nearest dates first."""
    by_amount = {}
    for j, b in enumerate(bank_rows):
        by_amount.setdefault(round(b["amount"], 2), []).append((j, b))
    ordinal = lambda d: datetime.strptime(d, "%Y-%m-%d").toordinal()
    candidates = []
    for i, s in enumerate(sheet_rows):
        for j, b in by_amount.get(round(s["amount"], 2), []):
            gap = abs(ordinal(s["date"]) - ordinal(b["date"]))
            if gap <= CARRY_DAYS:
                candidates.append((gap, i, j))
    used_sheet, used_bank = set(), set()
    for _, i, j in sorted(candidates):
        if i in used_sheet or j in used_bank:
            continue
        used_sheet.add(i)
        used_bank.add(j)
        yield sheet_rows[i], bank_rows[j]


def _carry(conn, s, b, categories):
    """Copy what was decided on spreadsheet row s onto bank row b. Returns 1 if b changed."""
    updates = {}
    mine, theirs = s["category_id"], b["category_id"]
    name = lambda c: categories.get(c, ("", ""))[0]
    kind = lambda c: categories.get(c, ("", ""))[1]
    # Your own label in the spreadsheet ("Dog sitter") usually says more than the bank's text
    # ("CUSTOMER WITHDRAWAL"), except a catch-all Other, or when both are just kinds of transfer.
    if (
        b["category_source"] != "manual"
        and mine is not None
        and mine != theirs
        and not (theirs is not None and name(mine) == "Other")
        and not (theirs is not None and kind(mine) == kind(theirs) == "transfer")
    ):
        updates["category_id"] = mine
        updates["category_source"] = "manual" if s["category_source"] == "manual" else "sheet"
    if s["date_override"] and not b["date_override"]:
        updates["date_override"] = s["date_override"]
    notes = s["notes"] or (
        f"Spreadsheet: {s['name']}" if "category_id" in updates and s["name"] not in ("Unlabeled", b["name"]) else None
    )
    if notes and not b["notes"]:
        updates["notes"] = notes
    if not updates:
        return 0
    conn.execute(
        f"UPDATE transactions SET {', '.join(f'{k} = ?' for k in updates)} WHERE id = ?", (*updates.values(), b["id"])
    )
    return 1
