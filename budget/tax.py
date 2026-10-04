"""Tax time page: what an accountant asks for, for one calendar year.

Checkbox items (categories with a tax checkbox, transactions ticked yes), each business account's income,
costs and profit, income by category, and notes that mention a CPA or taxes. Everything counts on the
effective date, like every other report, so a year here matches the Overview's year and the Business page.
"""
import csv
import io
import re
from datetime import date

from . import budgeting, business, reports

SECTIONS = {"checkbox": "Checkbox items", "business": "Business", "income": "Income", "notes": "Questions for your accountant"}
CSV_COLUMNS = ["section", "date", "account", "merchant", "category", "amount", "checkbox", "notes"]
# "cpa" as a word, or anything starting with "tax" (taxes, taxable, tax-deductible) except "taxi".
ACCOUNTANT_NOTE = re.compile(r"\bcpa\b|\btax(?!i)", re.IGNORECASE)

TXN_SELECT = """SELECT t.id, t.effective_date, t.date, t.amount, t.name, t.notes, t.flag, t.category_id,
                       a.name AS account, c.name AS category, c.flag AS checkbox
                FROM transactions t JOIN accounts a ON a.id = t.account_id
                LEFT JOIN categories c ON c.id = t.category_id"""
ORDER = " ORDER BY t.effective_date, t.date, t.id"


def default_year(today=None):
    """Last year until the end of April (tax season), then this year."""
    today = today or date.today()
    return today.year - 1 if today.month <= 4 else today.year


def _rows(conn, where, params):
    return [dict(r) for r in conn.execute(f"{TXN_SELECT} WHERE {where}{ORDER}", params)]


def checkboxes(conn, year):
    """[{label, categories, rows, total, n}] per checkbox label, plus the year's count still to check.

    total is the signed sum (money out is negative). Two categories with the same label share one box."""
    start, end = reports.bounds(year)
    boxes = {}
    for c in conn.execute("SELECT id, name, flag FROM categories WHERE flag IS NOT NULL ORDER BY sort, name"):
        boxes.setdefault(c["flag"], {"label": c["flag"], "categories": [], "rows": []})["categories"].append(dict(c))
    for r in _rows(conn, "t.flag = 'yes' AND c.flag IS NOT NULL AND t.effective_date >= ? AND t.effective_date < ?",
                   (start, end)):
        boxes[r["checkbox"]]["rows"].append(r)
    for b in boxes.values():
        b["total"] = round(sum(r["amount"] for r in b["rows"]), 2)
        b["n"] = len(b["rows"])
    to_check = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE flag = 'check' AND effective_date >= ? AND effective_date < ?", (start, end)
    ).fetchone()[0]
    return list(boxes.values()), to_check


def businesses(conn, year, today=None):
    """Per business account: the year's totals (the same numbers as the Business page) and its transactions."""
    start, end = reports.bounds(year)
    out = []
    for b in business.businesses(conn):
        r = business.report(conn, b, today)
        totals = dict(r["years"]).get(str(year)) or {"income": 0.0, "costs": 0.0, "profit": 0.0, "put_in": 0.0, "took_out": 0.0}
        cats = [c for c in (b["default_in_category"], b["default_out_category"]) if c]
        rows = _rows(conn, f"t.category_id IN ({', '.join('?' * len(cats))}) AND t.effective_date >= ? AND t.effective_date < ?",
                     (*cats, start, end))
        by_cat = {}
        for t in rows:
            by_cat.setdefault(t["category"], 0.0)
            by_cat[t["category"]] += t["amount"]
        out.append({"account": b, "totals": totals, "rows": rows,
                    "by_category": [{"name": k, "total": round(v, 2)} for k, v in sorted(by_cat.items(), key=lambda kv: kv[1], reverse=True)]})
    return out


def income(conn, year):
    """Income by category, the transactions behind it, uncategorized deposits, and paycheck retirement savings."""
    start, end = reports.bounds(year)
    work = budgeting.work_savings(conn, start, end)
    return {
        "categories": reports.by_category(conn, start, end, "income"),
        "rows": _rows(conn, "c.kind = 'income' AND t.effective_date >= ? AND t.effective_date < ?", (start, end)),
        "deposits": reports.uncategorized_deposits(conn, start, end),
        "work": work if work["paychecks"] else None,
    }


def notes(conn, year):
    """Transactions in the year whose notes mention a CPA or taxes."""
    start, end = reports.bounds(year)
    rows = _rows(conn, "(t.notes LIKE '%cpa%' OR t.notes LIKE '%tax%') AND t.effective_date >= ? AND t.effective_date < ?",
                 (start, end))
    return [r for r in rows if ACCOUNTANT_NOTE.search(r["notes"] or "")]


def summary(conn, year, today=None, with_business=True):
    boxes, to_check = checkboxes(conn, year)
    biz = businesses(conn, year, today) if with_business else []
    return {"year": year, "boxes": boxes, "to_check": to_check, "businesses": biz, "income": income(conn, year),
            "notes": notes(conn, year), "profit": round(sum(b["totals"]["profit"] for b in biz), 2)}


def _checkbox_text(r):
    if not r["checkbox"] or not r["flag"]:
        return ""
    return r["checkbox"] if r["flag"] == "yes" else f"To check: {r['checkbox']}"


def _cell(value):
    """Text a spreadsheet won't run as a formula."""
    value = "" if value is None else str(value)
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def csv_rows(data, section="all"):
    """(section, row) pairs for the export, one section or every one."""
    picked = list(SECTIONS) if section == "all" else [section]
    out = []
    for s in picked:
        if s == "checkbox":
            out += [(s, r) for b in data["boxes"] for r in b["rows"]]
        elif s == "business":
            out += [(s, r) for b in data["businesses"] for r in b["rows"]]
        elif s == "income":
            out += [(s, r) for r in data["income"]["rows"]]
        elif s == "notes":
            out += [(s, r) for r in data["notes"]]
    return out


def to_csv(data, section="all"):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for s, r in csv_rows(data, section):
        w.writerow([SECTIONS[s], r["effective_date"], _cell(r["account"]), _cell(r["name"]), _cell(r["category"] or "Uncategorized"),
                    f"{r['amount']:.2f}", _cell(_checkbox_text(r)), _cell(r["notes"])])
    return buf.getvalue()
