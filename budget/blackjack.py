"""Blackjack sessions from a tracker workbook, as results in a bankroll (cash) account.

Every sheet whose first row has "Date" and "AV Total" columns is read; each row with a real date and
a numeric AV Total is one session (the actual win or loss, in the Blackjack category). Rows without a
date, like monthly and all-time totals, are skipped. Importing again replaces the sessions from the
last import, so the tracker stays the record for results. Moving money between the bank and the
bankroll is a Transfer, as with any cash account.
"""
import datetime as dt
from pathlib import Path

import openpyxl

from .rules import sync_dates

CATEGORY = "Blackjack"


def _day(value):
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    for fmt in ("%b %d %Y", "%B %d %Y", "%m/%d/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _hours(value):
    if isinstance(value, dt.time):
        return value.hour + value.minute / 60
    if isinstance(value, dt.timedelta):
        return value.total_seconds() / 3600
    return value if isinstance(value, (int, float)) else None


def read_sessions(path):
    """[{date, location, result, ev, hours, sheet}] from every tracker sheet, oldest first."""
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    sessions = []
    for ws in wb.worksheets:
        rows = ws.iter_rows(values_only=True)
        head = [str(v).strip() if v is not None else "" for v in next(rows, [])]
        if "Date" not in head or "AV Total" not in head:
            continue
        col = {name: head.index(name) for name in ("Location", "Date", "AV Total", "EV Total", "Hrs") if name in head}
        for row in rows:
            get = lambda name: row[col[name]] if name in col and col[name] < len(row) else None  # noqa: E731
            day, result = _day(get("Date")), get("AV Total")
            if day is None or not isinstance(result, (int, float)):
                continue
            ev = get("EV Total")
            sessions.append({
                "date": day.isoformat(), "location": str(get("Location") or "Blackjack").strip(),
                "result": round(float(result), 2), "ev": round(float(ev), 2) if isinstance(ev, (int, float)) else None,
                "hours": _hours(get("Hrs")), "sheet": ws.title,
            })
    sessions.sort(key=lambda s: s["date"])
    return sessions


def import_tracker(conn, path, account_id):
    """Replace the account's blackjack sessions with the tracker's. Returns (sessions, net result)."""
    category = conn.execute("SELECT id FROM categories WHERE name = ?", (CATEGORY,)).fetchone()
    if category is None:
        category_id = conn.execute(
            "INSERT INTO categories (name, kind, sort) VALUES (?, 'income', 999)", (CATEGORY,)
        ).lastrowid
    else:
        category_id = category[0]
    sessions = read_sessions(path)
    conn.execute(
        "DELETE FROM imports WHERE kind = 'blackjack' AND account_id = ?", (account_id,)
    )  # their transactions go with them (ON DELETE CASCADE)
    import_id = conn.execute(
        "INSERT INTO imports (filename, account_id, kind, rows_read, rows_added) VALUES (?, ?, 'blackjack', ?, ?)",
        (Path(path).name, account_id, len(sessions), len(sessions)),
    ).lastrowid
    seen = {}
    for s in sessions:
        base = f"blackjack|{account_id}|{s['date']}|{s['location']}"
        seen[base] = seen.get(base, -1) + 1
        details = [f"{s['sheet']}"]
        if s["hours"]:
            details.append(f"{s['hours']:.1f} h")
        if s["ev"] is not None:
            details.append(f"expected {s['ev']:+,.2f}")
        conn.execute(
            """INSERT INTO transactions (account_id, import_id, date, effective_date, amount, raw_description, memo,
                   name, name_locked, category_id, category_source, dedupe_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 'manual', ?)""",
            (account_id, import_id, s["date"], s["date"], s["result"], f"Blackjack {s['location']}", ", ".join(details),
             f"Blackjack: {s['location']}", category_id, f"{base}|{seen[base]}"),
        )
    sync_dates(conn, "account_id = ?", (account_id,))
    return len(sessions), round(sum(s["result"] for s in sessions), 2)
