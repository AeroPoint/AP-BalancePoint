"""Parse bank CSV exports (US Bank first, generic Date/Description/Amount second) and store them."""
import csv
import io
import re
from collections import Counter
from datetime import datetime

from .rules import RuleEngine, normalize

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


def store_transactions(conn, account_id, filename, parsed, kind="csv", engine=None):
    """Insert parsed rows, skipping ones already imported.

    A row may carry "category_hint": (category_id, compatible category ids or None) from a
    hand-sorted source like the old spreadsheet. When the rules' category doesn't fit the hint
    (None means the hint only fills in uncategorized rows), the hint wins and is stored as
    'sheet', which later rule edits leave alone. Already-imported rows get the hint too.

    Returns (import_id, rows_read, rows_added, rows_categorized_by_hint).
    """
    engine = engine or RuleEngine(conn)
    cur = conn.execute(
        "INSERT INTO imports (filename, account_id, kind, rows_read) VALUES (?, ?, ?, ?)",
        (filename, account_id, kind, len(parsed)),
    )
    import_id = cur.lastrowid
    seen = Counter()
    added = hinted = 0
    for t in parsed:
        base = f"{account_id}|{t['date']}|{t['amount']:.2f}|{normalize(t['raw'])}"
        # The Nth identical charge on the same day gets its own key, so re-uploading an
        # overlapping export skips duplicates without dropping real repeat purchases.
        key = f"{base}|{seen[base]}"
        seen[base] += 1
        fixed_name = t.get("name")
        m = engine.resolve(t["raw"], t.get("mcc"), fixed_name)
        category_id, source = m.category_id, m.source
        if t.get("category_hint"):
            wanted, compatible = t["category_hint"]
            fits = m.category_id is not None and (compatible is None or m.category_id in compatible)
            if not fits:
                category_id, source = wanted, "sheet"
        cur = conn.execute(
            """INSERT OR IGNORE INTO transactions
               (account_id, import_id, date, amount, raw_description, memo, mcc,
                name, name_locked, category_id, category_source, dedupe_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, import_id, t["date"], t["amount"], t["raw"], t.get("memo"), t.get("mcc"),
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
    conn.execute("UPDATE imports SET rows_added = ? WHERE id = ?", (added, import_id))
    if added == 0:
        conn.execute("DELETE FROM imports WHERE id = ?", (import_id,))
    return import_id, len(parsed), added, hinted
