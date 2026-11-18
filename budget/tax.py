"""Tax time page: what an accountant asks for, for one calendar year.

Tax items (per category, chosen on the page: its ticked transactions, all of them, or none), each business account's income,
costs and profit, income by category, and notes that mention a CPA or taxes. Everything counts on the
effective date, like every other report, so a year here matches the Overview's year and the Business page.
"""
import csv
import io
import json
import re
from datetime import date

from . import budgeting, business, reports
from .balances import loan_payment, months_elapsed

SECTIONS = {"checkbox": "Tax items", "business": "Business", "income": "Income", "rental": "Rental",
            "notes": "Questions for your accountant"}
RENTAL_LABEL = "Rental property"  # the checkbox that marks a rental's costs, unless another is picked
RENTAL_SETUP_KEY = "tax_rental_setup"  # JSON {"label", "income": [category ids], "mortgage": [category ids]}
RENTAL_LOAN_KEY = "tax_rental_loan"  # loan_terms id of the rental's mortgage, to split payments
CSV_COLUMNS = ["section", "date", "account", "merchant", "category", "amount", "checkbox", "notes"]
# "cpa" as a word, or anything starting with "tax" (taxes, taxable, tax-deductible) except "taxi".
ACCOUNTANT_NOTE = re.compile(r"\bcpa\b|\btax(?!i)", re.IGNORECASE)

# What Tax time does with a category (categories.tax_mode): leave it out, count its ticked transactions, or count
# all of them. Unset: ticked when it has a checkbox, else out. All-in categories read as ticked, nothing to tick.
TAX_MODES = {"off": "Left out", "ticked": "Ticked ones", "all": "All of it"}
TICKED = "(t.flag = 'yes' OR c.tax_mode = 'all')"
TXN_SELECT = """SELECT t.id, t.effective_date, t.date, t.amount, t.name, t.notes, t.category_id,
                       CASE WHEN c.tax_mode = 'all' THEN 'yes' ELSE t.flag END AS flag,
                       a.name AS account, c.name AS category,
                       CASE WHEN c.tax_mode = 'all' THEN COALESCE(c.flag, c.name) ELSE c.flag END AS checkbox
                FROM transactions t JOIN accounts a ON a.id = t.account_id
                LEFT JOIN categories c ON c.id = t.category_id"""
ORDER = " ORDER BY t.effective_date, t.date, t.id"


def default_year(today=None):
    """Last year until the end of April (tax season), then this year."""
    today = today or date.today()
    return today.year - 1 if today.month <= 4 else today.year


def _rows(conn, where, params):
    return [dict(r) for r in conn.execute(f"{TXN_SELECT} WHERE {where}{ORDER}", params)]


def tax_mode(c):
    """'off', 'ticked' or 'all' for a category row (needs flag and tax_mode)."""
    mode = c["tax_mode"] if c["tax_mode"] in TAX_MODES else ("ticked" if c["flag"] else "off")
    return "off" if mode == "ticked" and not c["flag"] else mode


def tax_categories(conn):
    """Every category with what Tax time does with it, for the page's choices."""
    return [{**dict(c), "mode": tax_mode(c)} for c in conn.execute(
        "SELECT id, name, kind, flag, tax_mode FROM categories ORDER BY CASE kind WHEN 'expense' THEN 0 WHEN 'income' THEN 1 ELSE 2 END, sort, name")]


def save_tax_mode(conn, cid, mode):
    """Store a choice, or clear it when it's what the category would do anyway."""
    c = conn.execute("SELECT flag, NULL AS tax_mode FROM categories WHERE id = ?", (cid,)).fetchone()
    if c is None or mode not in TAX_MODES or (mode == "ticked" and not c["flag"]):
        return
    conn.execute("UPDATE categories SET tax_mode = ? WHERE id = ?", (None if mode == tax_mode(c) else mode, cid))


def checkboxes(conn, year):
    """[{label, categories, rows, total, n}] per section, plus the year's count still to check.

    A category on Tax time adds its ticked transactions (or all of them) under its checkbox's label, or its own
    name when it has none. total is the signed sum (money out is negative). Same label: one section."""
    start, end = reports.bounds(year)
    boxes, ticked, every = {}, [], []
    for c in tax_categories(conn):
        if c["mode"] == "off":
            continue
        label = c["flag"] or c["name"]
        boxes.setdefault(label, {"label": label, "categories": [], "rows": []})["categories"].append(c)
        (every if c["mode"] == "all" else ticked).append(c["id"])
    if not boxes:
        return [], 0
    ids = ticked + every
    marks = ", ".join("?" * len(ids))
    for r in _rows(conn, f"""t.category_id IN ({marks}) AND (t.flag = 'yes' OR c.tax_mode = 'all')
                             AND t.effective_date >= ? AND t.effective_date < ?""", (*ids, start, end)):
        boxes[r["checkbox"]]["rows"].append(r)
    for b in boxes.values():
        b["total"] = round(sum(r["amount"] for r in b["rows"]), 2)
        b["n"] = len(b["rows"])
    to_check = conn.execute(
        f"""SELECT COUNT(*) FROM transactions WHERE flag = 'check' AND category_id IN ({', '.join('?' * len(ticked)) or 'NULL'})
            AND effective_date >= ? AND effective_date < ?""", (*ticked, start, end)
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


def _owed_after(loan, n):
    """Balance after n monthly payments on an amortizing loan."""
    r = loan["annual_rate"] / 100 / 12
    if r == 0:
        return loan["principal"] * (1 - n / loan["term_months"])
    growth = (1 + r) ** n
    return loan["principal"] * growth - loan_payment(loan["principal"], loan["annual_rate"], loan["term_months"]) * (growth - 1) / r


def split_payment(loan, day):
    """(interest, principal) of the scheduled payment made on day, from the loan's terms."""
    n = max(1, min(months_elapsed(loan["start_date"], day), loan["term_months"]))
    interest = _owed_after(loan, n - 1) * loan["annual_rate"] / 100 / 12
    return interest, loan_payment(loan["principal"], loan["annual_rate"], loan["term_months"]) - interest


def rental_loans(conn):
    return [dict(r) for r in conn.execute(
        """SELECT lt.*, a.name AS account FROM loan_terms lt JOIN accounts a ON a.id = lt.account_id ORDER BY a.name""")]


def rental_setup(conn):
    """Which checkbox marks rental costs, which income categories are rent, which categories are the mortgage.

    What was picked on Tax time, else found by name: the "Rental property" checkbox, income categories named
    like "Rental…", and categories with "mortgage" in the name. Deleted categories drop out."""
    try:
        saved = json.loads(budgeting.get_setting(conn, RENTAL_SETUP_KEY, cast=str) or "{}")
    except ValueError:
        saved = {}
    cats = [dict(r) for r in conn.execute("SELECT id, name, kind, flag FROM categories ORDER BY sort, name")]
    ids = {c["id"] for c in cats}
    income = [c for c in cats if c["kind"] == "income"]
    spending = [c for c in cats if c["kind"] != "income"]
    labels = sorted({c["flag"] for c in cats if c["flag"]})
    by_name = {
        "label": RENTAL_LABEL,
        "income": [c["id"] for c in income if c["name"].lower().startswith("rental")],
        "mortgage": [c["id"] for c in spending if "mortgage" in c["name"].lower()],
    }
    pick = {k: saved[k] if k in saved else v for k, v in by_name.items()}
    pick["income"] = [i for i in pick["income"] if i in ids]
    pick["mortgage"] = [i for i in pick["mortgage"] if i in ids]
    return {**pick, "saved": bool(saved), "labels": labels, "income_categories": income, "spending_categories": spending}


def save_rental_setup(conn, label, income_ids, mortgage_ids):
    budgeting.set_setting(conn, RENTAL_SETUP_KEY, json.dumps(
        {"label": " ".join((label or "").split()), "income": sorted(set(income_ids)), "mortgage": sorted(set(mortgage_ids))}))


def rental(conn, year):
    """Rental income against its deductible costs, like a business. None without rental income or costs.

    Costs are transactions ticked with the rental checkbox (rental_setup) in any category. Ticked payments in a
    mortgage category are split with the rental loan's schedule: interest and escrow (property tax and
    insurance, the part of the payment above principal and interest) count; principal doesn't."""
    start, end = reports.bounds(year)
    setup = rental_setup(conn)
    income_ids = setup["income"] or [0]
    income_rows = _rows(conn, f"t.category_id IN ({', '.join('?' * len(income_ids))}) AND t.effective_date >= ? AND t.effective_date < ?",
                        (*income_ids, start, end))
    ticked = _rows(conn, f"{TICKED} AND c.flag = ? AND t.effective_date >= ? AND t.effective_date < ?",
                   (setup["label"], start, end)) if setup["label"] else []
    if not income_rows and not ticked:
        return None
    loans = rental_loans(conn)
    loan_id = budgeting.get_setting(conn, RENTAL_LOAN_KEY, cast=int)
    loan = next((l for l in loans if l["id"] == loan_id), None)
    payments = [r for r in ticked if r["category_id"] in setup["mortgage"] and r["amount"] < 0]
    others = [r for r in ticked if r not in payments]
    paid = -sum(r["amount"] for r in payments)
    interest = principal = escrow = 0.0
    if loan:
        for r in payments:
            i, p = split_payment(loan, r["effective_date"])
            interest += i
            principal += p
            escrow += max(-r["amount"] - i - p, 0.0)
    other_costs = -sum(r["amount"] for r in others)
    income_total = sum(r["amount"] for r in income_rows)
    deductible = (interest + escrow if loan else paid) + other_costs
    return {
        "income": round(income_total, 2), "income_rows": income_rows, "payments": payments, "others": others,
        "paid": round(paid, 2), "interest": round(interest, 2), "principal": round(principal, 2), "escrow": round(escrow, 2),
        "other_costs": round(other_costs, 2), "deductible": round(deductible, 2), "net": round(income_total - deductible, 2),
        "loan": loan, "loans": loans, "label": setup["label"],
    }


def notes(conn, year):
    """Transactions in the year whose notes mention a CPA or taxes."""
    start, end = reports.bounds(year)
    rows = _rows(conn, "(t.notes LIKE '%cpa%' OR t.notes LIKE '%tax%') AND t.effective_date >= ? AND t.effective_date < ?",
                 (start, end))
    return [r for r in rows if ACCOUNTANT_NOTE.search(r["notes"] or "")]


def questions(conn, year):
    """Questions written down for the accountant for this tax year: open ones first."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM tax_questions WHERE year = ? ORDER BY done, id", (year,))]


def save_question(conn, year, question, qid=None, answer=None, done=None):
    question = " ".join((question or "").split())
    if qid:
        # answer None (not sent, e.g. Reopen) keeps the saved one; an emptied box clears it.
        conn.execute("""UPDATE tax_questions SET question = COALESCE(NULLIF(?, ''), question),
                        answer = CASE WHEN ? THEN NULLIF(?, '') ELSE answer END,
                        done = COALESCE(?, done) WHERE id = ?""",
                     (question, answer is not None, (answer or "").strip(), None if done is None else int(bool(done)), qid))
    elif question:
        conn.execute("INSERT INTO tax_questions (year, question) VALUES (?, ?)", (year, question))


def summary(conn, year, today=None, with_business=True):
    boxes, to_check = checkboxes(conn, year)
    biz = businesses(conn, year, today) if with_business else []
    return {"year": year, "boxes": boxes, "to_check": to_check, "businesses": biz, "income": income(conn, year),
            "notes": notes(conn, year), "questions": questions(conn, year), "rental": rental(conn, year),
            "rental_setup": rental_setup(conn), "tax_categories": tax_categories(conn), "modes": TAX_MODES, "profit": round(sum(b["totals"]["profit"] for b in biz), 2)}


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
        elif s == "rental" and data.get("rental"):
            out += [(s, r) for r in data["rental"]["income_rows"] + data["rental"]["payments"] + data["rental"]["others"]]
        elif s == "notes":
            out += [(s, r) for r in data["notes"]]
    return out


def to_csv(data, section="all"):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    if section in ("all", "notes"):  # the written-down questions lead their section
        for q in data.get("questions", []):
            text = q["question"] + (f" | Answer: {q['answer']}" if q["answer"] else "") + (" | (done)" if q["done"] else "")
            w.writerow([SECTIONS["notes"], "", "", "", "", "", "", _cell(text)])
    for s, r in csv_rows(data, section):
        w.writerow([SECTIONS[s], r["effective_date"], _cell(r["account"]), _cell(r["name"]), _cell(r["category"] or "Uncategorized"),
                    f"{r['amount']:.2f}", _cell(_checkbox_text(r)), _cell(r["notes"])])
    return buf.getvalue()
