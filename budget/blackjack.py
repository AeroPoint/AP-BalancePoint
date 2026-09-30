"""Blackjack: sessions, research and training, and their results in a bankroll (cash) account.

A session is one casino visit; its tables are the games played there (one row each in the old tracker
workbook). A session's actual result is one transaction in the bankroll account, in the Blackjack
category, kept in step whenever the session is saved or deleted. Moving money between the bank and the
bankroll is a Transfer, as with any cash account.

Sessions are logged in the app. The old tracker workbook (data/source/Blackjack Tracker.xlsx) can be
imported, and importing again replaces only what came from a workbook, never what was entered here:
- sheets with Date and AV Total columns and real dates hold sessions. A row with no result that has the
  same (or a blank) date and location as the row above is another table of that visit.
- other sheets with a header row are research (one row per casino game), and what follows a
  summary label ("Average of Trip", "Home", "FULL DIRECTIONS") or "Spread A"-style bet tables are notes.
- a sheet with no header row is a note (Reno).
- the Training Tracker sheet is the practice log.
"""
import datetime as dt
import json
import re
from pathlib import Path

import openpyxl

from .rules import sync_dates

CATEGORY = "Blackjack"
ACCOUNT = "Blackjack Bankroll"
MILE_RATE = 0.67  # the tracker's own $ per mile for travel costs; settings key bj_mile_rate overrides

# A table's game and conditions, in the tracker's column order and with its column names.
RULE_KEYS = ["17", "RSA", "DAS", "Sur", "BJ", "Decks", "Double", "Cutoff", "SP#", "# Players", "N0", "Spread",
             "Min", "Max", "Play All", "Speed", "Deck Estimation", "Deck Divisor"]
RULE_LABELS = {"17": "Dealer soft 17", "Cutoff": "Cutoff (decks)", "SP#": "Split to (hands)", "N0": "N0",
               "Double": "Double on"}
SESSION_COLS = {"Location": "location", "Date": "date", "AV Total": "result", "Tip": "tip",
                "Miles Of Travel": "miles", "Flight": "flight", "Room n Board": "room_board",
                "Wearing": "wearing", "Notes": "notes"}
TABLE_COLS = {"Hrs": "hours", "EV Total": "ev_total", "EV Per Hour": "ev_hour"}
DERIVED = {"EV Per Hour", "AV Per Hour", "HR/N0"}  # formulas in the tracker, worked out on the page instead
TOTAL_LABELS = {"DECEMBER", "ALL TIME", "GRAND TOTAL", "SESSIONS METHOD"}
NOTE_LABELS = {"Average of Trip", "Home", "FULL DIRECTIONS"}


# ---------------------------------------------------------------- reading cells

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
        return round(value.hour + value.minute / 60 + value.second / 3600, 4)
    if isinstance(value, dt.timedelta):
        return round(value.total_seconds() / 3600, 4)
    return float(value) if isinstance(value, (int, float)) else None


def _num(value):
    return round(float(value), 2) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _clean(key, value):
    """A cell as it was typed, undoing Excel's guesses: "3:2" read as the time 3:02, "1-20" as a date."""
    if value is None or (isinstance(value, str) and (not value.strip() or value.strip().startswith("#"))):
        return None
    if key == "BJ" and isinstance(value, (int, float, dt.time)) and not isinstance(value, bool):
        h = _hours(value) * 24 if isinstance(value, (int, float)) else _hours(value)
        if isinstance(value, (int, float)) and value >= 1:
            return str(value)
        return f"{int(h)}:{round((h - int(h)) * 60)}"
    if key == "Spread" and isinstance(value, (dt.datetime, dt.date)):
        return f"{value.month}-{value.day}"
    if key == "Spread" and isinstance(value, (int, float)) and value > 20000:
        d = dt.date(1899, 12, 30) + dt.timedelta(days=int(value))
        return f"{d.month}-{d.day}"
    if isinstance(value, (dt.time, dt.timedelta)):
        return _hours(value)
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, float):
        return round(value, 4)
    return value.strip() if isinstance(value, str) else value


def _headers(row):
    """Column names from a header row. A blank first column is a status column; a blank column after
    EV or RoR is the same figure for the second spread; other blanks are extra notes."""
    names = []
    for i, v in enumerate(row):
        name = str(v).strip() if v is not None else ""
        if not name:
            prev = names[-1] if names else ""
            name = "Status" if i == 0 else f"{prev} (B)" if prev in ("EV", "RoR") else ""
        names.append(name)
    return names


# ---------------------------------------------------------------- the workbook

def read_workbook(path):
    """{"sessions": [...], "research": [...], "notes": [...], "training": [...]} from a tracker workbook."""
    wb = openpyxl.load_workbook(path, data_only=True)
    out = {"sessions": [], "research": [], "notes": [], "training": []}
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        if not rows or not any(v is not None for v in rows[0]):
            continue
        head = _headers(rows[0])
        if "Training" in ws.title:
            _read_training(ws.title, head, rows[1:], out)
        elif "AV Total" in head and "Date" in head and any(_day(r[head.index("Date")]) for r in rows[1:] if len(r) > head.index("Date")):
            _read_sessions(ws.title, head, rows[1:], out)
        elif "Location" in head:
            _read_research(ws.title, head, rows, out)
        else:
            text = "\n".join(str(v).strip() for r in rows for v in r if v is not None and str(v).strip())
            out["notes"].append({"region": ws.title, "title": None, "body": text, "source": ws.title})
    out["sessions"].sort(key=lambda s: s["date"])
    return out


def _read_sessions(sheet, head, rows, out):
    session = None
    for row in rows:
        cell = {h: row[i] for i, h in enumerate(head) if i < len(row) and h}
        extra = [str(row[i]).strip() for i, h in enumerate(head) if not h and i < len(row) and row[i] not in (None, "")]
        extra += [str(v).strip() for v in row[len(head):] if v not in (None, "")]
        location = str(cell.get("Location") or "").strip()
        day = _day(cell.get("Date"))
        rules = {k: _clean(k, cell.get(k)) for k in RULE_KEYS if _clean(k, cell.get(k)) is not None}
        table = {"hours": _hours(cell.get("Hrs")), "ev_total": _num(cell.get("EV Total")),
                 "ev_hour": _num(cell.get("EV Per Hour")), "rules": rules}
        if location.upper() in TOTAL_LABELS or not (location or day or rules):
            continue  # totals, or the tracker's side calculations
        result = _num(cell.get("AV Total"))
        continues = session is not None and result is None and (
            (not location or location == session["location"]) and (not day or day.isoformat() == session["date"])
        )
        if not continues:
            if day is None:
                continue
            session = {"date": day.isoformat(), "location": location or "Blackjack", "result": result,
                       "tip": None, "miles": None, "flight": None, "room_board": None, "wearing": None,
                       "notes": None, "casino_notes": None, "source": sheet, "tables": []}
            out["sessions"].append(session)
        session["tables"].append(table)
        for key in ("tip", "miles", "flight", "room_board"):
            col = next(k for k, v in SESSION_COLS.items() if v == key)
            if _num(cell.get(col)):
                session[key] = round((session[key] or 0) + _num(cell.get(col)), 2)
        for key, value in (("wearing", cell.get("Wearing")), ("notes", cell.get("Notes"))):
            if value and str(value).strip():
                session[key] = "\n".join(filter(None, [session[key], str(value).strip()]))
        if extra:
            # Unlabeled cells: short remarks in the 2024 sheet, the casino write-ups ("411") in 2025's.
            key = "casino_notes" if "Notes" in head else "notes"
            session[key] = "\n".join(filter(None, [session[key], *extra]))


def _read_research(sheet, head, rows, out):
    casino_i = head.index("Location")
    casino, notes_from = None, None
    for n, row in enumerate(rows[1:], start=1):
        label = str(row[casino_i]).strip() if casino_i < len(row) and row[casino_i] else ""
        if label in NOTE_LABELS:
            notes_from = n
            break
        fields = {}
        for i, h in enumerate(head):
            if i == casino_i or i >= len(row):
                continue
            value = _clean(h or "More", row[i])
            if value is None or value == "-":
                continue
            key = h or "More"
            fields[key] = f"{fields[key]} · {value}" if key in fields else value
        if not label and len(fields) < 3:
            continue  # leftover formulas under the table
        casino = label or casino
        if casino:
            out["research"].append({"region": sheet, "casino": casino, "fields": fields, "source": sheet})
    if notes_from is not None:
        _read_notes(sheet, head, rows[notes_from:], out)


def _read_notes(sheet, head, rows, out):
    """Summary rows ("Average of Trip", "Home", "FULL DIRECTIONS") and bet-spread tables."""
    tables = {}
    for r, row in enumerate(rows):
        label = next((str(v).strip() for v in row if isinstance(v, str) and str(v).strip() in NOTE_LABELS), None)
        if label:
            parts = []
            for i, v in enumerate(row):
                v = _clean(head[i] if i < len(head) else "", v)
                if v is None or v == label or v == "." or str(v).startswith("Spread "):
                    continue
                parts.append(v if str(v).startswith("http") else f"{head[i] or 'Value'}: {v}" if i < len(head) else str(v))
            out["notes"].append({"region": sheet, "title": label, "body": "\n".join(map(str, parts)), "source": sheet})
        for c, v in enumerate(row):
            if isinstance(v, str) and re.fullmatch(r"Spread [A-Z]", v.strip()):
                tables[(r, c)] = v.strip()
    for (r, c), title in tables.items():
        header = [str(v) for v in rows[r + 1][c:c + 3] if v is not None]
        lines = [" | ".join(header)]
        for row in rows[r + 2:]:
            cells = row[c:c + len(header)]
            if not cells or cells[0] is None:
                break
            lines.append(" | ".join("" if v is None else str(v) for v in cells))
        out["notes"].append({"region": sheet, "title": title, "body": "\n".join(lines), "source": sheet})


def _read_training(sheet, head, rows, out):
    names = ["Date", "Time", "Source", *head[3:]]
    for row in rows:
        day = _day(row[0]) if row else None
        if day is None:
            continue
        fields = {names[i]: _clean(names[i], v) for i, v in enumerate(row[2:], start=2) if i < len(names)}
        hours = _hours(row[1]) if len(row) > 1 else None
        out["training"].append({"date": day.isoformat(), "minutes": round(hours * 60) if hours else None,
                                "fields": {k: v for k, v in fields.items() if v is not None}, "source": sheet})


# ---------------------------------------------------------------- saving

def category_id(conn):
    row = conn.execute("SELECT id FROM categories WHERE name = ?", (CATEGORY,)).fetchone()
    if row:
        return row[0]
    return conn.execute("INSERT INTO categories (name, kind, sort) VALUES (?, 'income', 999)", (CATEGORY,)).lastrowid


def bankroll_account(conn):
    """The bankroll cash account (Blackjack Bankroll), created if missing."""
    row = conn.execute("SELECT id FROM accounts WHERE name = ?", (ACCOUNT,)).fetchone()
    if row:
        return row[0]
    return conn.execute("INSERT INTO accounts (name, kind) VALUES (?, 'cash')", (ACCOUNT,)).lastrowid


def _table_numbers(t):
    """Fill in EV total or EV per hour from the other, the way the tracker's formulas do."""
    hours, ev_total, ev_hour = t.get("hours"), t.get("ev_total"), t.get("ev_hour")
    if ev_total is None and ev_hour is not None and hours:
        ev_total = round(ev_hour * hours, 2)
    if ev_hour is None and ev_total is not None and hours:
        ev_hour = round(ev_total / hours, 2)
    return hours, ev_total, ev_hour


def save_session(conn, s, account_id=None):
    """Insert or update a session (s["id"] set = update) with its tables, and keep its bankroll
    transaction in step. Returns the session id."""
    account_id = account_id or bankroll_account(conn)
    cols = ("date", "location", "result", "tip", "miles", "flight", "room_board", "wearing", "notes",
            "casino_notes", "source")
    values = [s.get(c) for c in cols]
    values[cols.index("source")] = s.get("source") or "app"
    sid = s.get("id")
    if sid:
        conn.execute(f"UPDATE bj_sessions SET {', '.join(f'{c} = ?' for c in cols)} WHERE id = ?", (*values, sid))
        conn.execute("DELETE FROM bj_tables WHERE session_id = ?", (sid,))
    else:
        sid = conn.execute(f"INSERT INTO bj_sessions ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", values).lastrowid
    for i, t in enumerate(s.get("tables") or []):
        hours, ev_total, ev_hour = _table_numbers(t)
        conn.execute(
            "INSERT INTO bj_tables (session_id, sort, hours, ev_total, ev_hour, rules) VALUES (?, ?, ?, ?, ?, ?)",
            (sid, i, hours, ev_total, ev_hour, json.dumps(t.get("rules") or {})),
        )
    _sync_transaction(conn, sid, account_id)
    return sid


def delete_session(conn, sid):
    row = conn.execute("SELECT transaction_id FROM bj_sessions WHERE id = ?", (sid,)).fetchone()
    if row and row[0]:
        conn.execute("DELETE FROM transactions WHERE id = ?", (row[0],))
    conn.execute("DELETE FROM bj_sessions WHERE id = ?", (sid,))


def _sync_transaction(conn, sid, account_id):
    s = conn.execute("SELECT * FROM bj_sessions WHERE id = ?", (sid,)).fetchone()
    hours, ev = conn.execute("SELECT SUM(hours), SUM(ev_total) FROM bj_tables WHERE session_id = ?", (sid,)).fetchone()
    existing = s["transaction_id"] and conn.execute("SELECT id FROM transactions WHERE id = ?", (s["transaction_id"],)).fetchone()
    if not s["result"]:
        if existing:
            conn.execute("DELETE FROM transactions WHERE id = ?", (s["transaction_id"],))
        conn.execute("UPDATE bj_sessions SET transaction_id = NULL WHERE id = ?", (sid,))
        return
    memo = ", ".join(filter(None, [f"{hours:.1f} h" if hours else None, f"expected {ev:+,.2f}" if ev is not None else None]))
    values = (s["date"], s["date"], s["result"], f"Blackjack {s['location']}", memo, f"Blackjack: {s['location']}")
    if existing:
        conn.execute(
            "UPDATE transactions SET date = ?, effective_date = ?, amount = ?, raw_description = ?, memo = ?, name = ? WHERE id = ?",
            (*values, s["transaction_id"]),
        )
    else:
        tid = conn.execute(
            """INSERT INTO transactions (account_id, date, effective_date, amount, raw_description, memo, name,
                   name_locked, category_id, category_source, dedupe_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, 'manual', ?)""",
            (account_id, *values, category_id(conn), f"bjsession|{sid}"),
        ).lastrowid
        conn.execute("UPDATE bj_sessions SET transaction_id = ? WHERE id = ?", (tid, sid))
    sync_dates(conn, "account_id = ?", (account_id,))


def import_workbook(conn, path, account_id=None):
    """Replace everything that came from a workbook with this one's; sessions, research and training
    entered in the app stay. Returns counts and the net result of the imported sessions."""
    account_id = account_id or bankroll_account(conn)
    data = read_workbook(path)
    for (sid,) in conn.execute("SELECT id FROM bj_sessions WHERE source != 'app'").fetchall():
        delete_session(conn, sid)
    for table in ("bj_research", "bj_notes", "bj_training"):
        conn.execute(f"DELETE FROM {table} WHERE source != 'app'")
    # Results from the previous kind of tracker import (one transaction per row, no sessions).
    conn.execute("DELETE FROM imports WHERE kind = 'blackjack' AND account_id = ?", (account_id,))
    for s in data["sessions"]:
        save_session(conn, s, account_id)
    for i, r in enumerate(data["research"]):
        conn.execute("INSERT INTO bj_research (region, sort, casino, fields, source) VALUES (?, ?, ?, ?, ?)",
                     (r["region"], i, r["casino"], json.dumps(r["fields"]), r["source"]))
    for i, n in enumerate(data["notes"]):
        conn.execute("INSERT INTO bj_notes (region, sort, title, body, source) VALUES (?, ?, ?, ?, ?)",
                     (n["region"], i, n["title"], n["body"], n["source"]))
    for t in data["training"]:
        conn.execute("INSERT INTO bj_training (date, minutes, fields, source) VALUES (?, ?, ?, ?)",
                     (t["date"], t["minutes"], json.dumps(t["fields"]), t["source"]))
    return {
        "sessions": len(data["sessions"]), "tables": sum(len(s["tables"]) for s in data["sessions"]),
        "research": len(data["research"]), "notes": len(data["notes"]), "training": len(data["training"]),
        "net": round(sum(s["result"] or 0 for s in data["sessions"]), 2),
    }


# ---------------------------------------------------------------- the page

def sessions(conn):
    """Every session, oldest first, with its tables and the tracker's per-session figures."""
    tables = {}
    for t in conn.execute("SELECT * FROM bj_tables ORDER BY session_id, sort"):
        t = dict(t)
        t["rules"] = json.loads(t["rules"] or "{}")
        tables.setdefault(t["session_id"], []).append(t)
    out = []
    for s in conn.execute("SELECT * FROM bj_sessions ORDER BY date, id"):
        s = dict(s)
        s["tables"] = tables.get(s["id"], [])
        s["hours"] = round(sum(t["hours"] or 0 for t in s["tables"]), 4)
        evs = [t["ev_total"] for t in s["tables"] if t["ev_total"] is not None]
        s["ev"] = round(sum(evs), 2) if evs else None
        s["ev_hour"] = round(s["ev"] / s["hours"], 2) if s["ev"] is not None and s["hours"] else None
        s["av_hour"] = round(s["result"] / s["hours"], 2) if s["result"] is not None and s["hours"] else None
        out.append(s)
    return out


def totals(rows, mile_rate):
    """The tracker's ALL TIME and GRAND TOTAL figures for a list of sessions."""
    hours = sum(s["hours"] for s in rows)
    ev = sum(s["ev"] or 0 for s in rows)
    av = sum(s["result"] or 0 for s in rows)
    miles = sum(s["miles"] or 0 for s in rows)
    flight = sum(s["flight"] or 0 for s in rows)
    room = sum(s["room_board"] or 0 for s in rows)
    travel = miles * mile_rate + flight + room
    return {
        "sessions": len(rows), "hours": round(hours, 2), "ev": round(ev, 2), "av": round(av, 2),
        "ev_hour": round(ev / hours, 2) if hours else None, "av_hour": round(av / hours, 2) if hours else None,
        "miles": round(miles, 1), "mileage": round(miles * mile_rate, 2), "flight": round(flight, 2),
        "room_board": round(room, 2), "travel": round(travel, 2), "net": round(av - travel, 2),
        "tips": round(sum(s["tip"] or 0 for s in rows), 2),
    }


def by_year(rows, mile_rate):
    years = sorted({s["date"][:4] for s in rows}, reverse=True)
    return [(y, totals([s for s in rows if s["date"][:4] == y], mile_rate)) for y in years]


def running(rows):
    """Cumulative actual and expected results, session by session, for the chart."""
    av = ev = 0
    out = []
    for s in rows:
        av += s["result"] or 0
        ev += s["ev"] or 0
        out.append({"date": s["date"], "location": s["location"], "av": round(av, 2), "ev": round(ev, 2)})
    return out


def rules_line(rules):
    """The game at a glance: 6D · S17 · DAS · DA2 · SP3 · pen 1.25 · 1-20 · $25-500 · solo."""
    r = rules
    parts = []
    if r.get("Decks"):
        parts.append(f"{r['Decks']}D")
    if r.get("17") in ("H", "S"):
        parts.append(f"{r['17']}17")
    yn = lambda k, yes, no=None: yes if r.get(k) == "Y" else (no if r.get(k) == "N" else None)  # noqa: E731
    parts += filter(None, [yn("RSA", "RSA"), yn("DAS", "DAS", "no DAS"), yn("Sur", "Surrender")])
    if r.get("Double"):
        parts.append("DA2" if str(r["Double"]).lower() == "all" else f"double {r['Double']}")
    if r.get("BJ") and r["BJ"] != "3:2":
        parts.append(f"BJ {r['BJ']}")
    if r.get("SP#"):
        parts.append(f"SP{r['SP#']}")
    if r.get("Cutoff") not in (None, ""):
        parts.append(f"cut {r['Cutoff']}")
    if r.get("Spread"):
        parts.append(str(r["Spread"]))
    if r.get("Min") or r.get("Max"):
        parts.append(f"${r.get('Min', '?')}–{r.get('Max', '?')}")
    players = r.get("# Players")
    if isinstance(players, (int, float)) and players > 0:
        parts.append("solo" if players == 1 else f"{players:g} players")
    return " · ".join(parts)


def _key(name):
    return re.sub(r"[^a-z]", "", (name or "").lower())


def _region_of(location, regions):
    """Which research region a session's casino belongs to: "Riverside (Reno)" -> Reno,
    "Canyon Club (NV)" -> Nevada, or a casino-name match; None if nothing fits."""
    m = re.search(r"\(([^)]*)\)\s*$", location or "")
    tag = _key(m[1]) if m else ""
    name = _key(re.sub(r"\s*\([^)]*\)\s*$", "", location or ""))
    for region, casinos in regions.items():
        initials = "".join(w[0] for w in region.split()).lower()
        if tag and tag in (_key(region), initials):
            return region
    for region, casinos in regions.items():
        for c in casinos:
            k = _key(c)
            if name and k and (name.startswith(k[:5]) or k.startswith(name[:5])):
                return region
    return None


def research(conn, sessions_list=()):
    """[(region, [column names], [rows], [notes], [sessions with casino notes])] in workbook order.
    Casino notes written on sessions are shown under the region their casino belongs to; any that
    don't match a region come last, under "Other casinos"."""
    regions = {}
    for r in conn.execute("SELECT * FROM bj_research ORDER BY sort, id"):
        r = dict(r)
        r["fields"] = json.loads(r["fields"] or "{}")
        regions.setdefault(r["region"], {"rows": [], "notes": []})["rows"].append(r)
    for n in conn.execute("SELECT * FROM bj_notes ORDER BY sort, id"):
        n = dict(n)
        lines = n["body"].splitlines()
        n["table"] = [ln.split(" | ") for ln in lines] if len(lines) > 1 and all(" | " in ln for ln in lines) else None
        regions.setdefault(n["region"], {"rows": [], "notes": []})["notes"].append(n)
    casinos = {region: [r["casino"] for r in v["rows"]] for region, v in regions.items()}
    for s in sessions_list:
        if s.get("casino_notes"):
            region = _region_of(s["location"], casinos) or "Other casinos"
            regions.setdefault(region, {"rows": [], "notes": []}).setdefault("411", []).append(s)
    out = []
    for region, v in regions.items():
        columns = []
        for r in v["rows"]:
            columns += [k for k in r["fields"] if k not in columns]
        out.append((region, columns, v["rows"], v["notes"], sorted(v.get("411", []), key=lambda s: s["date"])))
    return out


def training(conn):
    rows = [dict(r) for r in conn.execute("SELECT * FROM bj_training ORDER BY date DESC, id DESC")]
    columns = []
    for r in rows:
        r["fields"] = json.loads(r["fields"] or "{}")
        columns += [k for k in r["fields"] if k not in columns]
    return columns, rows


def session_from_form(form):
    """A session dict from the Log a session form: repeated table fields are table0-hours, table1-hours..."""
    def num(name):
        v = (form.get(name) or "").replace("$", "").replace(",", "").strip()
        try:
            return round(float(v), 2) if v else None
        except ValueError:
            return None

    def hours(name):
        v = (form.get(name) or "").strip()
        m = re.fullmatch(r"(\d+):(\d{1,2})", v)
        if m:
            return round(int(m[1]) + int(m[2]) / 60, 4)
        return num(name)

    result = num("result")
    if result is not None and form.get("outcome") == "lost":
        result = -abs(result)
    tables = []
    for i in range(20):
        p = f"table{i}-"
        if not any(k.startswith(p) for k in form):
            continue
        rules = {}
        for key in RULE_KEYS:
            v = (form.get(p + key) or "").strip()
            if v:
                try:
                    v = int(v) if re.fullmatch(r"-?\d+", v) else float(v) if re.fullmatch(r"-?\d*\.\d+", v) else v
                except ValueError:
                    pass
                rules[key] = v
        t = {"hours": hours(p + "hours"), "ev_hour": num(p + "ev_hour"), "ev_total": num(p + "ev_total"), "rules": rules}
        if t["hours"] or t["ev_hour"] or t["ev_total"] or rules:
            tables.append(t)
    text = lambda k: (form.get(k) or "").strip() or None  # noqa: E731
    return {
        "id": int(form["id"]) if (form.get("id") or "").isdigit() else None,
        "date": text("date"), "location": text("location"), "result": result, "tip": num("tip"),
        "miles": num("miles"), "flight": num("flight"), "room_board": num("room_board"),
        "wearing": text("wearing"), "notes": text("notes"), "casino_notes": text("casino_notes"),
        "source": text("source") or "app", "tables": tables,
    }
