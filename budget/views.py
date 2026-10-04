"""Pages and small JSON endpoints."""
import json
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from . import blackjack, budgeting, business, personal, recurring, reports, seed, simplefin_import
from .balances import load_ledgers, loan_balance, loan_payment, month_end, month_range
from .csv_import import BAD_DATES_NOTE, CsvFormatError, flips_sign, parse_csv, replace_spreadsheet_rows, store_transactions
from .db import get_db, merge_category
from .excel_import import ensure_account, import_workbook
from .rules import RuleEngine, compile_pattern, merchant_key, normalize, reapply, sync_dates

bp = Blueprint("main", __name__)
MONTHS = reports.MONTH_NAMES
PER_PAGE = 100
MONTH_INPUT = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


# ---------------------------------------------------------------- helpers

def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _money_input(value):
    s = re.sub(r"[,$\s]", "", str(value or ""))
    if not s:
        return None
    negative = s.startswith("(") and s.endswith(")")
    try:
        amount = float(s.strip("()"))
    except ValueError:
        return None
    return round(-amount if negative else amount, 2)


def _month_input(value):
    value = (value or "").strip()
    return value if MONTH_INPUT.fullmatch(value) else None


def _date_input(value):
    try:
        return date.fromisoformat((value or "").strip()).isoformat()
    except ValueError:
        return None


def _months_between(earlier, later):
    return (int(later[:4]) * 12 + int(later[5:7])) - (int(earlier[:4]) * 12 + int(earlier[5:7]))


def _categories(conn):
    return conn.execute(
        "SELECT * FROM categories ORDER BY CASE kind WHEN 'expense' THEN 0 WHEN 'income' THEN 1 ELSE 2 END, name"
    ).fetchall()


def _accounts(conn):
    return conn.execute("SELECT * FROM accounts ORDER BY closed IS NOT NULL, sort, name").fetchall()


def _account_scope(conn, arg="acct"):
    """Account ids picked in a filter. An empty list means every account."""
    wanted = {i for i in (_int(v) for v in request.args.getlist(arg)) if i}
    known = {r["id"] for r in conn.execute("SELECT id FROM accounts")}
    wanted &= known
    return [] if not wanted or wanted == known else sorted(wanted)


def _back(default_endpoint, **values):
    return redirect(request.form.get("next") or url_for(default_endpoint, **values))


def upsert_rule(conn, match_on, pattern, rename_to=None, category_id=None):
    existing = conn.execute(
        "SELECT id FROM rules WHERE match_on = ? AND pattern = ? AND amount IS NULL", (match_on, pattern)
    ).fetchone()
    if existing:
        sets, vals = ["source = 'user'"], []
        if rename_to is not None:
            sets.append("rename_to = ?")
            vals.append(rename_to)
        if category_id is not None:
            sets.append("category_id = ?")
            vals.append(category_id)
        conn.execute(f"UPDATE rules SET {', '.join(sets)} WHERE id = ?", (*vals, existing["id"]))
    else:
        conn.execute(
            "INSERT INTO rules (match_on, pattern, rename_to, category_id, source) VALUES (?, ?, ?, ?, 'user')",
            (match_on, pattern, rename_to, category_id),
        )


def remember_category(conn, name, category_id):
    """Always put this merchant in this category, now and for future imports."""
    conn.execute("UPDATE rules SET category_id = ? WHERE match_on = 'raw' AND rename_to = ?", (category_id, name))
    upsert_rule(conn, "name", name, category_id=category_id)
    return reapply(conn, "name = ? AND category_source NOT IN ('manual', 'sheet')", (name,))


def rename_merchant(conn, txn, old, new_name):
    """Rename a merchant everywhere and teach the dictionary the new name."""
    conn.execute("UPDATE rules SET rename_to = ?, source = 'user' WHERE rename_to = ?", (new_name, old))
    conn.execute("UPDATE OR IGNORE rules SET pattern = ? WHERE match_on = 'name' AND pattern = ?", (new_name, old))
    if RuleEngine(conn).resolve(txn["raw_description"], txn["mcc"], amount=txn["amount"]).name != new_name:
        upsert_rule(conn, "raw", merchant_key(txn["raw_description"]), rename_to=new_name)
    changed = conn.execute("UPDATE transactions SET name = ? WHERE name = ?", (new_name, old)).rowcount
    return changed + reapply(conn, "name_locked = 0")


def _txn_json(conn, tid):
    row = conn.execute(
        """SELECT t.id, t.name, t.category_id, t.category_source, t.notes, c.name AS category_name,
                  t.date, t.date_override, t.effective_date
           FROM transactions t LEFT JOIN categories c ON c.id = t.category_id WHERE t.id = ?""",
        (tid,),
    ).fetchone()
    return dict(row) if row else None


@bp.app_template_filter("money")
def money_filter(value, cents=True):
    if value is None:
        return "—"
    body = f"{abs(value):,.2f}" if cents else f"{abs(value):,.0f}"
    return ("−$" if round(value, 2) < 0 else "$") + body


@bp.app_template_filter("month_label")
def month_label(key):
    y, m = str(key).split("-")[:2]
    return f"{MONTHS[int(m) - 1]} {y}"


@bp.app_template_filter("day_label")
def day_label(iso):
    d = date.fromisoformat(str(iso)[:10])
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


@bp.app_template_filter("hm")
def hours_label(hours):
    """1.75 -> "1:45"."""
    if not hours:
        return "—"
    minutes = round(hours * 60)
    return f"{minutes // 60}:{minutes % 60:02d}"


def features(conn):
    """Optional parts of the app. [features] in personal.toml decides; without it, each is on once it's
    in use (so an install that uses it keeps it) and off for a new one: Blackjack once there are sessions,
    Business once an account has default categories (Accounts -> Business accounts)."""
    chosen = current_app.config.get("FEATURES", {})
    blackjack = chosen.get("blackjack")
    if blackjack is None:
        blackjack = conn.execute("SELECT 1 FROM bj_sessions LIMIT 1").fetchone() is not None
    business_on = chosen.get("business")
    if business_on is None:
        business_on = conn.execute(
            "SELECT 1 FROM accounts WHERE default_in_category IS NOT NULL OR default_out_category IS NOT NULL LIMIT 1"
        ).fetchone() is not None
    return {"blackjack": blackjack, "business": business_on}


@bp.app_context_processor
def inject_nav():
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM transactions WHERE category_id IS NULL").fetchone()[0]
    return {"uncategorized_count": count, "endpoint": request.endpoint, "features": features(conn)}


# ---------------------------------------------------------------- dashboard & trends

@bp.route("/")
def dashboard():
    conn = get_db()
    acct = _account_scope(conn)
    ly, lm = reports.latest_month(conn, acct)
    view = "year" if request.args.get("view") == "year" else "month"
    year = _int(request.args.get("year"), ly)
    month = min(max(_int(request.args.get("month"), lm), 1), 12)

    if view == "year":
        start, end = reports.bounds(year)
        prev_bounds, label, prev_label = reports.bounds(year - 1), str(year), str(year - 1)
        trend = reports.monthly(conn, year, 1, 12, acct)
        averages, avg_months = {}, 0
        prev_link = url_for(".dashboard", view="year", year=year - 1, acct=acct)
        next_link = url_for(".dashboard", view="year", year=year + 1, acct=acct)
    else:
        start, end = reports.bounds(year, month)
        py, pm = reports.add_months(year, month, -1)
        ny, nm = reports.add_months(year, month, 1)
        prev_bounds, label, prev_label = reports.bounds(py, pm), f"{MONTHS[month - 1]} {year}", f"{MONTHS[pm - 1]} {py}"
        trend = reports.monthly(conn, *reports.add_months(year, month, -11), 12, acct)
        averages, avg_months = reports.category_averages(conn, year, month, accounts=acct)
        prev_link = url_for(".dashboard", view="month", year=py, month=pm, acct=acct)
        next_link = url_for(".dashboard", view="month", year=ny, month=nm, acct=acct)

    spending = reports.by_category(conn, start, end, "expense", acct)
    for row in spending:
        row["average"] = averages.get(row["id"])
    card = budgeting.scorecard(conn, year, month, acct) if view == "month" else None
    today = date.today()
    pace = budgeting.pace(conn, today) if view == "month" and (year, month) == (today.year, today.month) and not acct else None
    return render_template(
        "dashboard.html", card=card, pace=pace, saving=budgeting.savings_rate(conn, start, end, acct),
        year_groups=budgeting.by_group(conn, start, end, acct)[0] if view == "year" else None,
        view=view, year=year, month=month, months=MONTHS, acct=acct, accounts=_accounts(conn),
        years=sorted(set(reports.years(conn)) | {year}, reverse=True),
        label=label, prev_label=prev_label, prev_link=prev_link, next_link=next_link,
        current=reports.totals(conn, start, end, acct), previous=reports.totals(conn, *prev_bounds, acct),
        spending=spending, income=reports.by_category(conn, start, end, "income", acct),
        merchants=reports.by_merchant(conn, start, end, accounts=acct), trend=trend, avg_months=avg_months,
        deposits=reports.uncategorized_deposits(conn, start, end, acct),
        period_filter=f"{year}" if view == "year" else f"{year}-{month:02d}",
    )


@bp.route("/trends")
def trends():
    conn = get_db()
    acct = _account_scope(conn)
    all_years = reports.years(conn)
    year = _int(request.args.get("year"), all_years[0] if all_years else date.today().year)
    matrix, month_totals, active = reports.category_matrix(conn, year, acct)
    compare = sorted(y for y in all_years if year - 2 <= y <= year)
    yearly = []
    for y in sorted(all_years):
        t = reports.totals(conn, *reports.bounds(y), acct)
        t.update(budgeting.savings_rate(conn, *reports.bounds(y), acct), year=y)
        yearly.append(t)
    return render_template(
        "trends.html",
        year=year, years=all_years or [year], months=MONTHS, acct=acct, accounts=_accounts(conn),
        matrix=matrix, month_totals=month_totals, active=active,
        cumulative=reports.cumulative_spending(conn, compare, acct),
        stacked=reports.stacked_by_category(conn, year, accounts=acct),
        flow=reports.monthly(conn, year, 1, 12, acct), yearly=yearly,
    )


@bp.post("/settings/flex-target")
def save_flex_target():
    conn = get_db()
    target = _money_input(request.form.get("flex_target"))
    budgeting.set_setting(conn, "flex_target", round(target, 2) if target and target > 0 else None)
    conn.commit()
    flash(f"Flexible spending target: {money_filter(target, False)} a month." if target else "Cleared the flexible spending target.", "ok")
    return redirect(request.form.get("next") or url_for(".dashboard"))


@bp.route("/plan")
def plan():
    conn = get_db()
    count = min(max(_int(request.args.get("months"), 4), 1), 12)
    through = request.args.get("through", "")
    before = None
    if MONTH_INPUT.fullmatch(through):
        ny, nm = reports.add_months(int(through[:4]), int(through[5:]), 1)
        before = date(ny, nm, 1)
    months = budgeting.complete_months(conn, count, before)
    accounts = _accounts(conn)
    return render_template(
        "plan.html", base=budgeting.baseline(conn, months), projection=budgeting.project(conn),
        count=count, through=f"{months[-1][0]}-{months[-1][1]:02d}" if months else "",
        accounts=accounts, names={a["id"]: a["name"] for a in accounts},
        settings={k: budgeting.get_setting(conn, k) for k in budgeting.PLAN_KEYS},
        plan_account=budgeting.get_setting(conn, "plan_account", cast=int),
        backup_accounts=[budgeting.get_setting(conn, k, cast=int) for k in budgeting.BACKUP_KEYS],
        backup_mins=[budgeting.get_setting(conn, k) for k in budgeting.BACKUP_MIN_KEYS],
        this_month=date.today().strftime("%Y-%m"),
    )


@bp.post("/plan/settings")
def save_plan_settings():
    conn = get_db()
    for key in (*budgeting.PLAN_KEYS, *budgeting.BACKUP_MIN_KEYS):
        if key in request.form:
            value = _money_input(request.form.get(key))
            budgeting.set_setting(conn, key, round(value, 2) if value is not None else None)
    for key in ("plan_account", *budgeting.BACKUP_KEYS):
        if key in request.form:
            budgeting.set_setting(conn, key, _int(request.form.get(key)))
    conn.commit()
    flash("Saved the plan.", "ok")
    return redirect(url_for(".plan", **request.args))


@bp.post("/plan/items/save")
def save_plan_item():
    conn = get_db()
    form = request.form
    label = " ".join(form.get("label", "").split())
    amount = _money_input(form.get("amount"))
    if form.get("direction") == "less" and amount:
        amount = -abs(amount)
    start, end = _month_input(form.get("start_month")), _month_input(form.get("end_month"))
    if not (label and amount and start):
        flash("A plan change needs a name, an amount per month and a first month.", "error")
        return redirect(url_for(".plan") + "#changes")
    iid = _int(form.get("id"))
    if iid:
        conn.execute("UPDATE plan_items SET label = ?, amount = ?, start_month = ?, end_month = ? WHERE id = ?",
                     (label, amount, start, end, iid))
    else:
        conn.execute("INSERT INTO plan_items (label, amount, start_month, end_month) VALUES (?, ?, ?, ?)",
                     (label, amount, start, end))
    conn.commit()
    return redirect(url_for(".plan") + "#changes")


@bp.post("/plan/items/<int:iid>/delete")
def delete_plan_item(iid):
    conn = get_db()
    conn.execute("DELETE FROM plan_items WHERE id = ?", (iid,))
    conn.commit()
    return redirect(url_for(".plan") + "#changes")


# ---------------------------------------------------------------- transactions

@bp.route("/transactions")
def transactions():
    conn = get_db()
    args = request.args
    where, params = ["1 = 1"], []
    q = args.get("q", "").strip()
    if q:
        where.append("(t.name LIKE ? OR t.raw_description LIKE ? OR t.notes LIKE ?)")
        params += [f"%{q}%"] * 3
    category = args.get("category", "")
    if category == "none":
        where.append("t.category_id IS NULL")
    elif _int(category):
        where.append("t.category_id = ?")
        params.append(int(category))
    account_ids = [i for i in (_int(v) for v in args.getlist("account")) if i]
    if account_ids:
        where.append(f"t.account_id IN ({', '.join('?' * len(account_ids))})")
        params += account_ids
    period = args.get("period", "")
    if re.fullmatch(r"\d{4}(-\d{2})?", period):
        start, end = reports.bounds(int(period[:4]), int(period[5:]) if len(period) == 7 else None)
        where.append("t.effective_date >= ? AND t.effective_date < ?")
        params += [start, end]
    if args.get("source") in ("mcc", "manual", "sheet", "rule", "none"):
        where.append("t.category_source = ?")
        params.append(args["source"])
    if args.get("name"):
        where.append("t.name = ?")
        params.append(args["name"])
    if args.get("one_off") == "1":
        where.append("t.one_off = 1")
    flag = args.get("flag", "")
    if flag == "check":
        where.append("t.flag = 'check'")
    elif flag.startswith("yes:") and _int(flag[4:]):
        where.append("t.flag = 'yes' AND t.category_id = ?")
        params.append(int(flag[4:]))

    clause = " AND ".join(where)
    page = max(_int(args.get("page"), 1), 1)
    summary = conn.execute(
        f"SELECT COUNT(*) AS n, COALESCE(SUM(t.amount), 0) AS total FROM transactions t WHERE {clause}", params
    ).fetchone()
    rows = conn.execute(
        f"""SELECT t.*, a.name AS account_name, c.name AS category_name, c.flag AS category_flag
            FROM transactions t JOIN accounts a ON a.id = t.account_id
            LEFT JOIN categories c ON c.id = t.category_id
            WHERE {clause} ORDER BY t.effective_date DESC, t.date DESC, t.id DESC LIMIT ? OFFSET ?""",
        (*params, PER_PAGE, (page - 1) * PER_PAGE),
    ).fetchall()
    periods = [r[0] for r in conn.execute("SELECT DISTINCT substr(effective_date, 1, 7) FROM transactions ORDER BY 1 DESC")]
    return render_template(
        "transactions.html",
        rows=rows, summary=summary, page=page, pages=max(1, -(-summary["n"] // PER_PAGE)),
        categories=_categories(conn), accounts=_accounts(conn),
        periods=periods, years=sorted({p[:4] for p in periods}, reverse=True),
        flagged=[dict(r) for r in conn.execute("SELECT id, name, flag FROM categories WHERE flag IS NOT NULL ORDER BY sort, name")],
        to_check=conn.execute("SELECT COUNT(*) FROM transactions WHERE flag = 'check'").fetchone()[0],
    )


@bp.post("/transactions/add")
def add_transaction():
    """A transaction no bank export will show, like spending from cash on hand."""
    conn = get_db()
    form = request.form
    back = form.get("next") or url_for(".transactions")
    account_id, day = _int(form.get("account_id")), _date_input(form.get("date"))
    name, amount = " ".join(form.get("name", "").split()), _money_input(form.get("amount"))
    if not (account_id and day and name and amount):
        flash("A transaction needs an account, a date, what it was and an amount.", "error")
        return redirect(back)
    amount = abs(amount) if form.get("direction") == "in" else -abs(amount)
    category_id, source = _int(form.get("category_id")), "manual"
    if not category_id:
        match = RuleEngine(conn).resolve(name, None, name, amount, account_id)
        category_id, source = match.category_id, match.source if match.category_id else "none"
    conn.execute(
        """INSERT INTO transactions (account_id, import_id, date, effective_date, amount, raw_description, name,
               name_locked, category_id, category_source, notes, dedupe_key)
           VALUES (?, NULL, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)""",
        (account_id, day, day, amount, name, name, category_id, source,
         " ".join(form.get("notes", "").split()) or None, f"manual|{uuid4().hex}"),
    )
    sync_dates(conn, "account_id = ?", (account_id,))
    conn.commit()
    flash(f"Added {name}: {money_filter(amount)} on {day}.", "ok")
    return redirect(back)


@bp.post("/transactions/<int:tid>/delete")
def delete_transaction(tid):
    conn = get_db()
    # Only hand-entered rows: bank rows come back on the next upload, so remove their import instead.
    removed = conn.execute("DELETE FROM transactions WHERE id = ? AND import_id IS NULL", (tid,)).rowcount
    conn.commit()
    flash("Deleted the transaction." if removed else "Only transactions you added by hand can be deleted here.",
          "ok" if removed else "error")
    return redirect(request.form.get("next") or url_for(".transactions"))


@bp.post("/api/transactions/<int:tid>")
def update_transaction(tid):
    conn = get_db()
    data = request.get_json(silent=True) or {}
    txn = conn.execute("SELECT * FROM transactions WHERE id = ?", (tid,)).fetchone()
    if txn is None:
        return jsonify(error="Transaction not found"), 404
    remember = bool(data.get("remember"))
    changed = 0

    if "name" in data:
        new_name = " ".join(str(data["name"]).split())
        # The page renames one row first, then offers "rename everywhere" with the old name.
        old_name = str(data.get("previous_name") or txn["name"])
        if new_name and remember and new_name != old_name:
            changed += rename_merchant(conn, txn, old_name, new_name)
        elif new_name and new_name != txn["name"]:
            conn.execute("UPDATE transactions SET name = ?, name_locked = 1 WHERE id = ?", (new_name, tid))
        txn = conn.execute("SELECT * FROM transactions WHERE id = ?", (tid,)).fetchone()

    if "category_id" in data:
        category_id = _int(data["category_id"])
        if remember and category_id:
            conn.execute("UPDATE transactions SET category_source = 'rule' WHERE id = ?", (tid,))
            changed += remember_category(conn, txn["name"], category_id)
        conn.execute(
            "UPDATE transactions SET category_id = ?, category_source = ? WHERE id = ?",
            (category_id, ("rule" if remember else "manual") if category_id else "none", tid),
        )

    if "date" in data:
        # The bank's date is kept; this only changes which date the transaction counts on.
        new_date = str(data["date"] or "").strip()
        if new_date:
            try:
                date.fromisoformat(new_date)
            except ValueError:
                return jsonify(error="Use a date like 2026-09-01."), 400
        conn.execute("UPDATE transactions SET date_override = ? WHERE id = ?", (new_date or None, tid))

    if "notes" in data:
        conn.execute("UPDATE transactions SET notes = ? WHERE id = ?", (str(data["notes"]).strip() or None, tid))

    if "one_off" in data:
        conn.execute("UPDATE transactions SET one_off = ? WHERE id = ?", (1 if data["one_off"] else 0, tid))

    if "flag" in data:  # the category's checkbox: 'yes', 'check' (to look at) or cleared
        conn.execute("UPDATE transactions SET flag = ? WHERE id = ?", (data["flag"] if data["flag"] in ("yes", "check") else None, tid))

    sync_dates(conn, "id = ?", (tid,))
    conn.commit()
    return jsonify(ok=True, changed=changed, transaction=_txn_json(conn, tid))


# ---------------------------------------------------------------- bulk categorize

@bp.route("/categorize")
def categorize():
    conn = get_db()
    mode = "guessed" if request.args.get("mode") == "guessed" else "uncategorized"
    cond = "t.category_id IS NULL" if mode == "uncategorized" else "t.category_source = 'mcc'"
    groups = conn.execute(
        f"""SELECT t.name, COUNT(*) AS n, SUM(t.amount) AS total, MAX(t.date) AS last_date,
                   MIN(t.raw_description) AS sample, MAX(c.id) AS category_id, MAX(c.name) AS category_name
            FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
            WHERE {cond} GROUP BY t.name ORDER BY n DESC, ABS(SUM(t.amount)) DESC LIMIT 400"""
    ).fetchall()
    guessed = conn.execute("SELECT COUNT(*) FROM transactions WHERE category_source = 'mcc'").fetchone()[0]
    return render_template("categorize.html", groups=groups, mode=mode, categories=_categories(conn), guessed=guessed)


@bp.post("/api/merchants/category")
def categorize_merchant():
    conn = get_db()
    data = request.get_json(silent=True) or {}
    name, category_id = str(data.get("name") or ""), _int(data.get("category_id"))
    if not name or not category_id:
        return jsonify(error="Pick a category"), 400
    if data.get("remember", True):
        changed = remember_category(conn, name, category_id)
    else:
        changed = conn.execute(
            "UPDATE transactions SET category_id = ?, category_source = 'manual' "
            "WHERE name = ? AND category_source IN ('none', 'mcc')",
            (category_id, name),
        ).rowcount
        sync_dates(conn, "name = ?", (name,))
    conn.commit()
    return jsonify(ok=True, changed=changed)


# ---------------------------------------------------------------- merchant dictionary & rules

@bp.route("/rules")
def rules_page():
    conn = get_db()
    tab = "name" if request.args.get("tab") == "name" else "raw"
    q = request.args.get("q", "").strip()
    source = request.args.get("source", "")
    where, params = ["r.match_on = ?"], [tab]
    if q:
        where.append("(r.pattern LIKE ? OR r.rename_to LIKE ? OR c.name LIKE ?)")
        params += [f"%{q}%"] * 3
    if source in ("user", "config", "excel", "builtin"):
        where.append("r.source = ?")
        params.append(source)
    rules = conn.execute(
        f"""SELECT r.*, c.name AS category_name FROM rules r LEFT JOIN categories c ON c.id = r.category_id
            WHERE {' AND '.join(where)}
            ORDER BY CASE r.source WHEN 'user' THEN 0 WHEN 'excel' THEN 1 ELSE 2 END, r.pattern""",
        params,
    ).fetchall()

    test_raw, test_result = request.args.get("test", "").strip(), None
    if test_raw:
        m = RuleEngine(conn).resolve(test_raw)
        cat = conn.execute("SELECT name FROM categories WHERE id = ?", (m.category_id,)).fetchone()
        test_result = {"name": m.name, "category": cat["name"] if cat else None, "source": m.source,
                       "suggested_pattern": merchant_key(test_raw)}
    counts = {r["match_on"]: r["n"] for r in conn.execute("SELECT match_on, COUNT(*) AS n FROM rules GROUP BY match_on")}
    return render_template(
        "rules.html", tab=tab, q=q, source=source, rules=rules, counts=counts,
        categories=_categories(conn), test_raw=test_raw, test_result=test_result,
    )


@bp.post("/rules/save")
def save_rule():
    conn = get_db()
    form = request.form
    match_on = "name" if form.get("match_on") == "name" else "raw"
    pattern = form.get("pattern", "").strip()
    if match_on == "raw" and not pattern.startswith("re:"):
        pattern = normalize(pattern)
    rename_to = " ".join(form.get("rename_to", "").split()) or None
    category_id = _int(form.get("category_id"))
    amount = _money_input(form.get("amount"))  # blank matches any amount
    if not pattern:
        flash("A rule needs text to match.", "error")
        return _back(".rules_page", tab=match_on)
    try:
        compile_pattern(pattern)
    except re.error as exc:
        flash(f"That regular expression is invalid: {exc}", "error")
        return _back(".rules_page", tab=match_on)
    if not rename_to and not category_id:
        flash("A rule needs a new name, a category, or both.", "error")
        return _back(".rules_page", tab=match_on)

    rid = _int(form.get("id"))
    try:
        if rid:
            conn.execute(
                "UPDATE rules SET pattern = ?, rename_to = ?, category_id = ?, amount = ?, source = 'user' WHERE id = ?",
                (pattern, rename_to, category_id, amount, rid),
            )
        else:
            conn.execute(
                """INSERT INTO rules (match_on, pattern, rename_to, category_id, amount, source)
                   VALUES (?, ?, ?, ?, ?, 'user')""",
                (match_on, pattern, rename_to, category_id, amount),
            )
    except sqlite3.IntegrityError:
        flash(f"A rule for “{pattern}” already exists — edit that one instead.", "error")
        return _back(".rules_page", tab=match_on)
    changed = reapply(conn)
    conn.commit()
    flash(f"Saved “{pattern}”. {changed} transaction{'s' if changed != 1 else ''} updated.", "ok")
    return _back(".rules_page", tab=match_on)


@bp.post("/rules/<int:rid>/delete")
def delete_rule(rid):
    conn = get_db()
    row = conn.execute("SELECT pattern, match_on FROM rules WHERE id = ?", (rid,)).fetchone()
    conn.execute("DELETE FROM rules WHERE id = ?", (rid,))
    changed = reapply(conn)
    conn.commit()
    if row:
        flash(f"Deleted “{row['pattern']}”. {changed} transactions updated.", "ok")
    return _back(".rules_page", tab=row["match_on"] if row else "raw")


# ---------------------------------------------------------------- categories

@bp.route("/categories")
def categories_page():
    conn = get_db()
    rows = conn.execute(
        """SELECT c.*, COUNT(t.id) AS n, COALESCE(SUM(t.amount), 0) AS total,
                  (SELECT COUNT(*) FROM rules r WHERE r.category_id = c.id) AS rule_count
           FROM categories c LEFT JOIN transactions t ON t.category_id = c.id
           GROUP BY c.id ORDER BY CASE c.kind WHEN 'expense' THEN 0 WHEN 'income' THEN 1 ELSE 2 END, c.name"""
    ).fetchall()
    return render_template("categories.html", rows=rows, groups=seed.SPENDING_GROUPS)


@bp.post("/categories/save")
def save_category():
    conn = get_db()
    name = " ".join(request.form.get("name", "").split())
    kind = request.form.get("kind")
    if not name or kind not in ("income", "expense", "transfer"):
        flash("A category needs a name and a type.", "error")
        return redirect(url_for(".categories_page"))
    cid = _int(request.form.get("id"))
    snap = int(request.form.get("snap_to_month") == "1")
    grp = request.form.get("grp")
    flag = " ".join(request.form.get("flag", "").split()) or None
    if kind == "expense":
        grp = grp if grp in {g for g, _, _ in seed.SPENDING_GROUPS} else "flexible"
    elif kind == "transfer":
        grp = "saving" if grp == "saving" else None
    else:
        grp = None
    try:
        if cid:
            conn.execute(
                "UPDATE categories SET name = ?, kind = ?, snap_to_month = ?, grp = ?, flag = ? WHERE id = ?",
                (name, kind, snap, grp, flag, cid),
            )
            sync_dates(conn, "category_id = ?", (cid,))
        else:
            conn.execute(
                "INSERT INTO categories (name, kind, sort, snap_to_month, grp, flag) VALUES (?, ?, 999, ?, ?, ?)",
                (name, kind, snap, grp, flag),
            )
        conn.commit()
        flash(f"Saved category “{name}”.", "ok")
    except sqlite3.IntegrityError:
        flash(f"There's already a category named “{name}”.", "error")
    return redirect(url_for(".categories_page"))


@bp.post("/categories/merge")
def merge_categories():
    conn = get_db()
    source, target = _int(request.form.get("source")), _int(request.form.get("target"))
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM categories")}
    if source not in names or target not in names or source == target:
        flash("Pick two different categories to merge.", "error")
        return redirect(url_for(".categories_page"))
    n = conn.execute("SELECT COUNT(*) FROM transactions WHERE category_id = ?", (source,)).fetchone()[0]
    merge_category(conn, source, target)
    sync_dates(conn, "category_id = ?", (target,))
    conn.commit()
    flash(f"Merged “{names[source]}” into “{names[target]}” ({n:,} transactions).", "ok")
    return redirect(url_for(".categories_page"))


@bp.post("/categories/<int:cid>/delete")
def delete_category(cid):
    conn = get_db()
    conn.execute("DELETE FROM categories WHERE id = ?", (cid,))
    conn.execute("UPDATE transactions SET category_source = 'none' WHERE category_id IS NULL")
    changed = reapply(conn, "category_id IS NULL")
    conn.commit()
    flash(f"Category deleted. Its transactions were re-run through your rules ({changed} re-categorized).", "ok")
    return redirect(url_for(".categories_page"))


# ---------------------------------------------------------------- accounts

@bp.route("/accounts")
def accounts_page():
    conn = get_db()
    latest = conn.execute(
        "SELECT MAX(m) FROM (SELECT MAX(substr(date, 1, 7)) AS m FROM transactions UNION ALL SELECT MAX(month) FROM balances)"
    ).fetchone()[0]
    rows = conn.execute(
        """SELECT a.*,
                  (SELECT COUNT(*) FROM transactions t WHERE t.account_id = a.id) AS n,
                  (SELECT MIN(date) FROM transactions t WHERE t.account_id = a.id) AS first_date,
                  (SELECT MAX(date) FROM transactions t WHERE t.account_id = a.id) AS last_date,
                  (SELECT month FROM balances b WHERE b.account_id = a.id ORDER BY month DESC LIMIT 1) AS balance_month,
                  (SELECT amount FROM balances b WHERE b.account_id = a.id ORDER BY month DESC LIMIT 1) AS balance,
                  (SELECT MAX(month) FROM balances b WHERE b.account_id = a.id AND ABS(amount) >= 0.5) AS last_nonzero
           FROM accounts a ORDER BY a.closed IS NOT NULL, a.sort, a.name"""
    ).fetchall()
    today = date.today().isoformat()
    ledgers = load_ledgers(conn)
    accounts = []
    for r in rows:
        a = dict(r)
        activity = max(filter(None, [(r["last_date"] or "")[:7], r["last_nonzero"]]), default=None)
        # Suggest closing accounts that have gone quiet, but never close anything on our own.
        a["quiet_since"] = activity if (
            not r["closed"] and activity and latest and _months_between(activity, latest) >= 4
        ) else None
        a["today"] = ledgers[r["id"]].on(today)
        a["from_loans"] = bool(ledgers[r["id"]].calculated_loans(today))
        a["side"] = seed.account_side(r["kind"])
        # Loan calculations replace quiet-account hints: a mortgage with no new balances isn't closed.
        if ledgers[r["id"]].loans:
            a["quiet_since"] = None
        accounts.append(a)
    loans = []
    for row in conn.execute(
        "SELECT l.*, a.name AS account_name FROM loan_terms l JOIN accounts a ON a.id = l.account_id ORDER BY a.name, l.start_date"
    ):
        loan = dict(row)
        loan["payment"] = loan_payment(loan["principal"], loan["annual_rate"], loan["term_months"])
        loan["balance_today"] = loan_balance(
            loan["principal"], loan["annual_rate"], loan["term_months"], loan["start_date"], today
        )
        loans.append(loan)
    paychecks = []
    for row in conn.execute(
        "SELECT p.*, a.name AS account_name FROM paycheck_savings p LEFT JOIN accounts a ON a.id = p.account_id "
        "ORDER BY p.pattern, p.start_date"
    ):
        entry = dict(row)
        entry["yours"], entry["employer"] = budgeting.contribution(row)
        paychecks.append(entry)
    return render_template(
        "accounts.html", accounts=accounts, kinds=seed.ACCOUNT_KINDS, groups=seed.ACCOUNT_GROUPS, loans=loans,
        paychecks=paychecks, retirement_accounts=[a for a in accounts if a["side"] == "asset"],
        liability_accounts=[a for a in accounts if a["side"] == "liability"], categories=_categories(conn),
        category_names={c["id"]: c["name"] for c in _categories(conn)},
    )


@bp.post("/accounts/defaults")
def save_account_defaults():
    """A business account's own categories for money in and out; re-categorizes what's already there."""
    conn = get_db()
    aid = _int(request.form.get("account_id"))
    if not aid:
        return redirect(url_for(".accounts_page") + "#business")
    money_in, money_out = _int(request.form.get("default_in_category")), _int(request.form.get("default_out_category"))
    conn.execute("UPDATE accounts SET default_in_category = ?, default_out_category = ? WHERE id = ?", (money_in, money_out, aid))
    changed = reapply(conn, "account_id = ?", (aid,))
    conn.commit()
    name = conn.execute("SELECT name FROM accounts WHERE id = ?", (aid,)).fetchone()[0]
    flash(f"{name}: " + ("default categories saved" if money_in or money_out else "default categories cleared")
          + f"; {changed} transaction{'s' if changed != 1 else ''} re-categorized. Ones you picked by hand stay.", "ok")
    return redirect(url_for(".accounts_page") + "#business")


@bp.post("/loans/save")
def save_loan():
    conn = get_db()
    form = request.form
    account_id = _int(form.get("account_id"))
    principal = _money_input(form.get("principal"))
    try:
        rate = float(str(form.get("annual_rate", "")).replace("%", "").strip())
        years = float(str(form.get("years", "")).strip())
    except ValueError:
        rate = years = None
    start = _date_input(form.get("start_date"))
    counts_from = _date_input(form.get("counts_from"))
    if not (account_id and principal and principal > 0 and rate is not None and rate >= 0 and years and years > 0 and start):
        flash("A loan needs its account, the amount borrowed, the interest rate, the length in years and a start date.", "error")
        return redirect(url_for(".accounts_page") + "#loans")
    term_months = round(years * 12)
    values = (account_id, " ".join(form.get("label", "").split()) or None, principal, rate, term_months, start, counts_from)
    lid = _int(form.get("id"))
    if lid:
        conn.execute(
            """UPDATE loan_terms SET account_id = ?, label = ?, principal = ?, annual_rate = ?, term_months = ?,
                   start_date = ?, counts_from = ? WHERE id = ?""",
            (*values, lid),
        )
    else:
        conn.execute(
            """INSERT INTO loan_terms (account_id, label, principal, annual_rate, term_months, start_date, counts_from)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            values,
        )
    conn.commit()
    flash(f"Saved the loan: {money_filter(loan_payment(principal, rate, term_months))} a month in principal and interest.", "ok")
    return redirect(url_for(".accounts_page") + "#loans")


def _percent(value):
    try:
        return float(str(value or "").replace("%", "").strip())
    except ValueError:
        return None


@bp.post("/paychecks/save")
def save_paycheck():
    conn = get_db()
    form = request.form
    pattern = " ".join(form.get("pattern", "").upper().split())
    base = _money_input(form.get("base_pay"))
    pct, rate, cap = _percent(form.get("employee_pct")), _percent(form.get("match_rate")), _percent(form.get("match_cap_pct"))
    start = _date_input(form.get("start_date"))
    if not (pattern and base and base > 0 and pct is not None and start):
        flash("A paycheck entry needs the paycheck's bank text, base pay per paycheck, your % and a start date.", "error")
        return redirect(url_for(".accounts_page") + "#paychecks")
    values = (pattern, _int(form.get("account_id")), start, base, pct, rate or 0, cap or 0)
    pid = _int(form.get("id"))
    if pid:
        conn.execute(
            """UPDATE paycheck_savings SET pattern = ?, account_id = ?, start_date = ?, base_pay = ?, employee_pct = ?,
                   match_rate = ?, match_cap_pct = ? WHERE id = ?""",
            (*values, pid),
        )
    else:
        conn.execute(
            """INSERT INTO paycheck_savings (pattern, account_id, start_date, base_pay, employee_pct, match_rate, match_cap_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            values,
        )
    conn.commit()
    yours, employer = budgeting.contribution(dict(zip(
        ("base_pay", "employee_pct", "match_rate", "match_cap_pct"), (base, pct, rate or 0, cap or 0))))
    flash(f"Saved: each paycheck puts {money_filter(yours)} of yours and {money_filter(employer)} from your employer "
          f"into retirement from {start} on.", "ok")
    return redirect(url_for(".accounts_page") + "#paychecks")


@bp.post("/paychecks/<int:pid>/delete")
def delete_paycheck(pid):
    conn = get_db()
    conn.execute("DELETE FROM paycheck_savings WHERE id = ?", (pid,))
    conn.commit()
    flash("Deleted the paycheck entry.", "ok")
    return redirect(url_for(".accounts_page") + "#paychecks")


@bp.post("/loans/<int:lid>/delete")
def delete_loan(lid):
    conn = get_db()
    conn.execute("DELETE FROM loan_terms WHERE id = ?", (lid,))
    conn.commit()
    flash("Deleted the loan. If it was the account's last one, the account goes back to balances you enter.", "ok")
    return redirect(url_for(".accounts_page") + "#loans")


@bp.post("/accounts/save")
def save_account():
    conn = get_db()
    form = request.form
    name = " ".join(form.get("name", "").split())
    kind = form.get("kind") if form.get("kind") in seed.KIND else "other"
    institution = " ".join(form.get("institution", "").split()) or None
    opened, closed = _month_input(form.get("opened")), _month_input(form.get("closed"))
    since = _month_input(form.get("bank_from"))
    # Only forms that show the checkbox change it (an unticked box sends nothing).
    flip = int(bool(form.get("csv_flip_sign"))) if "flip_sign_shown" in form else None
    if not name:
        flash("An account needs a name.", "error")
        return redirect(url_for(".accounts_page"))
    if opened and closed and closed < opened:
        flash(f"“{name}” can't close before the month it opened.", "error")
        return redirect(url_for(".accounts_page"))
    aid = _int(form.get("id"))
    try:
        if aid:
            conn.execute(
                "UPDATE accounts SET name = ?, kind = ?, institution = ?, opened = ?, closed = ?, bank_from = ?, "
                "csv_flip_sign = COALESCE(?, csv_flip_sign) WHERE id = ?",
                (name, kind, institution, opened, closed, since, flip, aid),
            )
        else:
            conn.execute(
                "INSERT INTO accounts (name, kind, institution, opened, closed, bank_from, csv_flip_sign) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (name, kind, institution, opened, closed, since, flip or 0),
            )
        conn.commit()
        flash(f"Saved “{name}”." + (f" Marked closed as of {month_label(closed)}." if closed else ""), "ok")
    except sqlite3.IntegrityError:
        flash(f"There's already an account named “{name}”.", "error")
    return redirect(url_for(".accounts_page"))


@bp.post("/accounts/<int:aid>/close")
def close_account(aid):
    conn = get_db()
    month = _month_input(request.form.get("month")) or date.today().strftime("%Y-%m")
    row = conn.execute("SELECT name FROM accounts WHERE id = ?", (aid,)).fetchone()
    conn.execute("UPDATE accounts SET closed = ? WHERE id = ?", (month, aid))
    conn.commit()
    if row:
        flash(f"Marked “{row['name']}” closed as of {month_label(month)}. Its history stays in every report.", "ok")
    return redirect(url_for(".accounts_page"))


@bp.post("/accounts/<int:aid>/merge")
def merge_account(aid):
    conn = get_db()
    target = _int(request.form.get("target"))
    source_row = conn.execute("SELECT * FROM accounts WHERE id = ?", (aid,)).fetchone()
    target_row = conn.execute("SELECT * FROM accounts WHERE id = ?", (target,)).fetchone()
    if not source_row or not target_row or aid == target:
        flash("Pick a different account to merge into.", "error")
        return redirect(url_for(".accounts_page"))
    # Re-key moved transactions to the target so future uploads still spot duplicates. A row that
    # can't move because the target already has it is the same transaction twice, so it's dropped.
    # Only keys that start with the account id are re-keyed; others (pending charges, blackjack sessions,
    # hand entries) carry their own form and keep it, with a pending charge's account part updated.
    conn.execute(
        "UPDATE OR IGNORE transactions SET dedupe_key = ? || substr(dedupe_key, length(?) + 1) "
        "WHERE account_id = ? AND dedupe_key LIKE ?",
        (f"sfpending|{target}|", f"sfpending|{aid}|", aid, f"sfpending|{aid}|%"),
    )
    moved = conn.execute(
        "UPDATE OR IGNORE transactions SET account_id = ?, dedupe_key = CASE WHEN dedupe_key LIKE ? "
        "THEN ? || substr(dedupe_key, instr(dedupe_key, '|')) ELSE dedupe_key END WHERE account_id = ?",
        (target, f"{aid}|%", str(target), aid),
    ).rowcount
    duplicates = conn.execute("DELETE FROM transactions WHERE account_id = ?", (aid,)).rowcount
    conn.execute(
        "INSERT OR IGNORE INTO balances (account_id, month, amount, as_of, source) "
        "SELECT ?, month, amount, as_of, source FROM balances WHERE account_id = ?",
        (target, aid),
    )
    conn.execute("UPDATE imports SET account_id = ? WHERE account_id = ?", (target, aid))
    conn.execute("UPDATE loan_terms SET account_id = ? WHERE account_id = ?", (target, aid))
    # Everything else that points at the old account follows it: bank sync, paycheck savings, the plan,
    # and its business categories when the target has none of its own.
    conn.execute("UPDATE simplefin_accounts SET account_id = ? WHERE account_id = ?", (target, aid))
    conn.execute("UPDATE paycheck_savings SET account_id = ? WHERE account_id = ?", (target, aid))
    for key in ("plan_account", *budgeting.BACKUP_KEYS):
        if budgeting.get_setting(conn, key, cast=int) == aid:
            budgeting.set_setting(conn, key, target)
    if not (target_row["default_in_category"] or target_row["default_out_category"]):
        conn.execute("UPDATE accounts SET default_in_category = ?, default_out_category = ? WHERE id = ?",
                     (source_row["default_in_category"], source_row["default_out_category"], target))
    conn.execute("DELETE FROM accounts WHERE id = ?", (aid,))
    conn.commit()
    flash(
        f"Merged “{source_row['name']}” into “{target_row['name']}”: moved {moved} transactions"
        + (f" and dropped {duplicates} that were already there." if duplicates else "."),
        "ok",
    )
    return redirect(url_for(".accounts_page"))


@bp.post("/accounts/<int:aid>/delete")
def delete_account(aid):
    conn = get_db()
    row = conn.execute("SELECT name FROM accounts WHERE id = ?", (aid,)).fetchone()
    deleted = conn.execute(
        "DELETE FROM accounts WHERE id = ? AND NOT EXISTS (SELECT 1 FROM transactions WHERE account_id = ?)", (aid, aid)
    ).rowcount
    conn.commit()
    if deleted:
        flash(f"Deleted “{row['name']}” and its balance history.", "ok")
    elif row:
        flash(f"“{row['name']}” has transactions. Mark it closed or merge it into another account instead.", "error")
    return redirect(url_for(".accounts_page"))


# ---------------------------------------------------------------- net worth

@bp.route("/net-worth", methods=["GET", "POST"])
def net_worth():
    conn = get_db()
    today = date.today().isoformat()
    month = _month_input(request.values.get("month")) or today[:7]
    # Balances are for a day: today in the current month, otherwise the month's last day.
    as_of = _date_input(request.values.get("as_of")) or (today if month == today[:7] else month_end(month))
    month = as_of[:7]
    ledgers = load_ledgers(conn)
    accounts = []
    for ledger in ledgers.values():
        a = dict(ledger.account)
        info = seed.KIND.get(a["kind"], seed.KIND["other"])
        a.update(side=info["side"], group=info["group"], kind_label=info["label"])
        accounts.append(a)
    open_accounts = [
        a for a in accounts
        if (not a["opened"] or a["opened"] <= month) and (not a["closed"] or a["closed"] >= month)
    ]

    if request.method == "POST":
        saved = cleared = 0
        for a in open_accounts:
            field = request.form.get(f"acct-{a['id']}")
            if field is None:
                continue
            value = _money_input(field)
            if value is None:
                if request.form.get(f"had-{a['id']}"):  # a box that showed a saved balance was emptied
                    cleared += conn.execute(
                        "DELETE FROM balances WHERE account_id = ? AND month = ? AND as_of = ?", (a["id"], month, as_of)
                    ).rowcount
                continue
            conn.execute(
                """INSERT INTO balances (account_id, month, amount, as_of, source) VALUES (?, ?, ?, ?, 'manual')
                   ON CONFLICT (account_id, month) DO UPDATE
                     SET amount = excluded.amount, as_of = excluded.as_of, source = 'manual'""",
                (a["id"], month, value, as_of),
            )
            saved += 1
        conn.commit()
        flash(
            f"Saved {saved} balance{'s' if saved != 1 else ''} as of {day_label(as_of)}."
            + (f" Cleared {cleared}." if cleared else ""),
            "ok",
        )
        return redirect(url_for(".net_worth", month=month, as_of=as_of))

    rows = []
    for a in open_accounts:
        ledger = ledgers[a["id"]]
        stored = conn.execute(
            "SELECT amount, as_of FROM balances WHERE account_id = ? AND month = ?", (a["id"], month)
        ).fetchone()
        anchor, since = ledger.since_anchor(as_of)
        scheduled = ledger.calculated_loans(as_of)
        if scheduled:
            hint = (f"Calculated from {len(scheduled)} loan{'s' if len(scheduled) != 1 else ''}, "
                    f"{money_filter(ledger.monthly_payment(as_of))} a month")
        elif anchor is None:
            hint = "No balance entered yet"
        elif not ledger.has_transactions and ledger.synced_on(as_of) == anchor:
            hint = f"From the bank sync, {day_label(anchor[0])}"
        elif anchor[0] == as_of:
            hint = "Entered for this day"
        elif ledger.has_transactions:
            hint = (f"{money_filter(anchor[1])} on {day_label(anchor[0])}, "
                    f"plus {since} transaction{'s' if since != 1 else ''} since")
        else:
            hint = f"Last entered {money_filter(anchor[1])} on {day_label(anchor[0])}"
        rows.append(dict(
            a, hint=hint, estimate=ledger.on(as_of), scheduled=bool(scheduled),
            exact=stored["amount"] if stored and stored["as_of"] == as_of and not scheduled else None,
        ))

    first = conn.execute(
        "SELECT MIN(m) FROM (SELECT MIN(month) AS m FROM balances UNION ALL SELECT MIN(substr(date, 1, 7)) FROM transactions)"
    ).fetchone()[0]
    months = month_range(first, today[:7]) if first else []
    series = []
    for a in accounts:
        # Each month is pinned to its 1st, so a month's number doesn't drift as the month goes on. An account
        # with no balance by the 1st (its first one came later that month) counts from that first balance.
        ledger = ledgers[a["id"]]
        values = []
        for m in months:
            v = ledger.on(f"{m}-01")
            if v is None:
                first_in_month = next((d for d, _ in ledger.anchors if d[:7] == m and d <= today), None)
                v = ledger.on(first_in_month) if first_in_month else None
            values.append(v)
        now = ledgers[a["id"]].on(today)
        if any(v is not None for v in values) or now is not None:  # a new account shows before its first 1st
            series.append({**{k: a[k] for k in ("id", "name", "kind", "side", "group", "opened", "closed")},
                           "values": values, "now": now})
    year, mon = int(month[:4]), int(month[5:])
    py, pm = reports.add_months(year, mon, -1)
    ny, nm = reports.add_months(year, mon, 1)
    return render_template(
        "net_worth.html",
        month=month, as_of=as_of, accounts=accounts, rows=rows, groups=seed.ACCOUNT_GROUPS,
        payload={"month": month, "today": today, "months": months, "accounts": series},
        prev_month=f"{py}-{pm:02d}", next_month=f"{ny}-{nm:02d}",
    )


# ---------------------------------------------------------------- data upload

@bp.route("/upload", methods=["GET", "POST"])
def upload():
    conn = get_db()
    if request.method == "POST":
        account_id = _int(request.form.get("account_id"))
        new_account = " ".join(request.form.get("new_account", "").split())
        if new_account:
            account_id = ensure_account(conn, new_account, request.form.get("kind", "checking"))
        files = [f for f in request.files.getlist("files") if f and f.filename]
        if not account_id:
            flash("Pick which account these transactions belong to.", "error")
        elif not files:
            flash("Choose at least one CSV file.", "error")
        if account_id and files and "flip_sign_shown" in request.form:
            # The checkbox starts out as the account's setting, so leaving it alone keeps it.
            conn.execute("UPDATE accounts SET csv_flip_sign = ? WHERE id = ?",
                         (int(bool(request.form.get("csv_flip_sign"))), account_id))
        flip = bool(account_id) and flips_sign(conn, account_id)
        for f in files if account_id else []:
            content = f.read()
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            (Path(current_app.config["UPLOAD_DIR"]) / f"{stamp}-{secure_filename(f.filename)}").write_bytes(content)
            if f.filename.lower().endswith((".xlsx", ".xls")):
                flash(f"{f.filename}: spreadsheets go in the Excel import section below.", "error")
                continue
            try:
                text = content.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = content.decode("latin-1")
            try:
                parsed = parse_csv(text, flip_sign=flip)
            except CsvFormatError as exc:
                flash(f"{f.filename}: {exc}", "error")
                continue
            _, read, added, _, before = store_transactions(conn, account_id, f.filename, parsed)
            replaced, carried = replace_spreadsheet_rows(conn, account_id, parsed, f.filename)
            flash(
                f"{f.filename}: added {added} of {read} transactions ({read - added - before} were already imported)."
                + (f" Skipped {before} from before the account's “bank exports from” month." if before else "")
                + (" " + BAD_DATES_NOTE.format(n=parsed.bad_dates) if parsed.bad_dates else "")
                + (f" Replaced {replaced} spreadsheet entries for the same account and dates" if replaced else "")
                + (f", keeping your categories, dates and notes on {carried} matching bank rows." if carried
                   else "." if replaced else ""),
                "error" if parsed.bad_dates else "ok",
            )
        conn.commit()
        return redirect(url_for(".upload"))

    imports = conn.execute(
        """SELECT i.*, a.name AS account_name,
                  (SELECT MIN(date) FROM transactions WHERE import_id = i.id) AS first_date,
                  (SELECT MAX(date) FROM transactions WHERE import_id = i.id) AS last_date,
                  (SELECT COUNT(*) FROM transactions WHERE import_id = i.id) AS remaining
           FROM imports i LEFT JOIN accounts a ON a.id = i.account_id ORDER BY i.id DESC"""
    ).fetchall()
    last_account = conn.execute("SELECT account_id FROM imports WHERE kind = 'csv' ORDER BY id DESC LIMIT 1").fetchone()
    return render_template(
        "upload.html",
        accounts=_accounts(conn),
        imports=imports,
        last_account_id=last_account["account_id"] if last_account else None,
        workbooks=sorted(p.name for p in Path(current_app.config["SOURCE_DIR"]).glob("*.xlsx")),
        sheet_configured=bool(personal.load(current_app.config["PERSONAL_CONFIG"]).spreadsheet),
    )


@bp.post("/upload/excel")
def upload_excel():
    conn = get_db()
    source_dir = Path(current_app.config["SOURCE_DIR"])
    upload_file = request.files.get("workbook")
    if upload_file and upload_file.filename:
        path = source_dir / secure_filename(upload_file.filename)
        upload_file.save(path)
    else:
        path = source_dir / Path(request.form.get("existing", "")).name
    if not path.is_file():
        flash("Choose a workbook to import.", "error")
        return redirect(url_for(".upload"))
    through = request.form.get("through")
    try:
        through_date = date.fromisoformat(through) if through else None
    except ValueError:
        through_date = None
    result = import_workbook(conn, path, through_date, personal.load(current_app.config["PERSONAL_CONFIG"]))
    if not result["configured"]:
        flash("There's nothing to read yet: add a [spreadsheet] section to data/personal.toml "
              "(personal.example.toml shows the format), then restart the app.", "error")
        return redirect(url_for(".upload"))
    ledger = ", ".join(f"{acct}: {added} new" for acct, _, added in result["ledger"]) or "no ledger rows"
    sorted_by = result["hand_sorted"]
    flash(
        f"Imported {path.name} through {result['through']}. {result['rules_added']} dictionary entries; {ledger}. "
        f"{sum(sorted_by.values()):,} transactions matched your spreadsheet categories "
        f"({sorted_by.get('formula', 0):,} from your category formulas, {sorted_by.get('color', 0):,} from cell colors), "
        f"{result['recategorized']:,} of them changed. {result['balances']:,} month-end balances."
        + (f" Skipped {result['skipped_for_bank_data']:,} entries on dates your bank exports already cover."
           if result["skipped_for_bank_data"] else "")
        + (f" Set up {result['loans_added']} loan schedule{'s' if result['loans_added'] != 1 else ''} from its balance formulas."
           if result["loans_added"] else ""),
        "ok",
    )
    return redirect(url_for(".upload"))


@bp.post("/upload/blackjack")
def upload_blackjack():
    conn = get_db()
    f = request.files.get("tracker")
    name = " ".join(request.form.get("account", "").split()) or "Blackjack Bankroll"
    if not f or not f.filename.lower().endswith(".xlsx"):
        flash("Choose the tracker workbook (.xlsx).", "error")
        return redirect(url_for(".upload"))
    path = Path(current_app.config["UPLOAD_DIR"]) / f"{datetime.now():%Y%m%d-%H%M%S}-{secure_filename(f.filename)}"
    f.save(path)
    account_id = ensure_account(conn, name, "cash")
    try:
        got = blackjack.import_workbook(conn, path, account_id)
    except Exception as exc:  # a workbook that isn't a tracker
        conn.rollback()
        flash(f"{f.filename}: couldn't read it ({exc}).", "error")
        return redirect(url_for(".upload"))
    conn.commit()
    flash(f"{name}: {got['sessions']} sessions ({got['tables']} tables), {got['research']} research rows and "
          f"{got['training']} practice sessions from {f.filename}, net {money_filter(got['net'])}. They replace "
          "the previous workbook import; sessions logged on the Bankroll page stay.", "ok")
    return redirect(url_for(".bankroll"))


@bp.post("/imports/<int:iid>/delete")
def delete_import(iid):
    conn = get_db()
    removed = conn.execute("SELECT COUNT(*) FROM transactions WHERE import_id = ?", (iid,)).fetchone()[0]
    conn.execute("DELETE FROM imports WHERE id = ?", (iid,))
    conn.commit()
    flash(f"Removed that import and its {removed} transactions.", "ok")
    return redirect(url_for(".upload"))


# ---------------------------------------------------------------- blackjack bankroll

RESEARCH_COLUMNS = ["Status", "Adress", "Decks", "Pen", "Min", "Max", "Spread", "EV", "RoR", "N0", "Double",
                    "RSA Rule", "17 Rule", "DAS Rule", "SP", "Open", "Other"]


@bp.route("/bankroll")
def bankroll():
    conn = get_db()
    rate = budgeting.get_setting(conn, "bj_mile_rate", blackjack.MILE_RATE)
    rows = blackjack.sessions(conn)
    research = blackjack.research(conn, rows)
    training_columns, training = blackjack.training(conn)
    editing = next((s for s in rows if s["id"] == _int(request.args.get("edit"))), None)
    locations = sorted({s["location"] for s in rows} | {r["casino"] for _, _, region_rows, _, _ in research for r in region_rows})
    account_id = conn.execute("SELECT id FROM accounts WHERE name = ?", (blackjack.ACCOUNT,)).fetchone()
    balance = load_ledgers(conn)[account_id[0]].on(date.today().isoformat()) if account_id else None
    return render_template(
        "bankroll.html", sessions=list(reversed(rows)), lifetime=blackjack.totals(rows, rate),
        years=blackjack.by_year(rows, rate), running=blackjack.running(rows), research=research,
        training=training, training_columns=training_columns, rate=rate, editing=editing, locations=locations,
        rule_keys=blackjack.RULE_KEYS, rule_labels=blackjack.RULE_LABELS, rules_line=blackjack.rules_line,
        research_columns=RESEARCH_COLUMNS, balance=balance, account_name=blackjack.ACCOUNT,
        today=date.today().isoformat(),
    )


@bp.post("/bankroll/sessions/save")
def save_bj_session():
    conn = get_db()
    s = blackjack.session_from_form(request.form)
    if not (s["date"] and _date_input(s["date"]) and s["location"]):
        flash("A session needs a date and a location.", "error")
        return redirect(url_for(".bankroll", edit=s["id"]) + "#log")
    sid = blackjack.save_session(conn, s)
    conn.commit()
    flash(f"Saved {s['location']}, {day_label(s['date'])}"
          + (f": {'+' if s['result'] > 0 else ''}{money_filter(s['result'])}" if s["result"] is not None else "") + ".", "ok")
    return redirect(url_for(".bankroll") + f"#s{sid}")


@bp.post("/bankroll/sessions/<int:sid>/delete")
def delete_bj_session(sid):
    conn = get_db()
    blackjack.delete_session(conn, sid)
    conn.commit()
    flash("Session deleted, with its result in the bankroll.", "ok")
    return redirect(url_for(".bankroll") + "#sessions")


def _fields_from_form(form, prefix="field:"):
    """{column: value} from inputs named field:<column>, numbers kept as numbers."""
    out = {}
    for key in form:
        if key.startswith(prefix) and form.get(key, "").strip():
            v = form[key].strip()
            out[key[len(prefix):]] = int(v) if re.fullmatch(r"-?\d+", v) else float(v) if re.fullmatch(r"-?\d*\.\d+", v) else v
    return out


@bp.post("/bankroll/research/save")
def save_bj_research():
    conn = get_db()
    form = request.form
    region, casino = " ".join(form.get("region", "").split()), " ".join(form.get("casino", "").split())
    if not (region and casino):
        flash("A research row needs a region and a casino.", "error")
        return redirect(url_for(".bankroll") + "#research")
    fields = json.dumps(_fields_from_form(form))
    rid = _int(form.get("id"))
    if rid:
        conn.execute("UPDATE bj_research SET region = ?, casino = ?, fields = ? WHERE id = ?", (region, casino, fields, rid))
    else:
        sort = conn.execute("SELECT COALESCE(MAX(sort), 0) + 1 FROM bj_research").fetchone()[0]
        conn.execute("INSERT INTO bj_research (region, sort, casino, fields) VALUES (?, ?, ?, ?)", (region, sort, casino, fields))
    conn.commit()
    flash(f"Saved {casino} under {region}.", "ok")
    return redirect(url_for(".bankroll") + "#research")


@bp.post("/bankroll/research/<int:rid>/delete")
def delete_bj_research(rid):
    conn = get_db()
    conn.execute("DELETE FROM bj_research WHERE id = ?", (rid,))
    conn.commit()
    return redirect(url_for(".bankroll") + "#research")


@bp.post("/bankroll/training/save")
def save_bj_training():
    conn = get_db()
    form = request.form
    day = _date_input(form.get("date"))
    if not day:
        flash("A practice session needs a date.", "error")
        return redirect(url_for(".bankroll") + "#training")
    conn.execute("INSERT INTO bj_training (date, minutes, fields) VALUES (?, ?, ?)",
                 (day, _int(form.get("minutes")), json.dumps(_fields_from_form(form))))
    conn.commit()
    return redirect(url_for(".bankroll") + "#training")


@bp.post("/bankroll/training/<int:tid>/delete")
def delete_bj_training(tid):
    conn = get_db()
    conn.execute("DELETE FROM bj_training WHERE id = ?", (tid,))
    conn.commit()
    return redirect(url_for(".bankroll") + "#training")


@bp.post("/bankroll/mile-rate")
def save_bj_mile_rate():
    conn = get_db()
    rate = _money_input(request.form.get("rate"))
    budgeting.set_setting(conn, "bj_mile_rate", rate if rate and rate > 0 else None)
    conn.commit()
    return redirect(url_for(".bankroll") + "#years")


# ---------------------------------------------------------------- businesses

@bp.route("/business")
def business_page():
    conn = get_db()
    return render_template("business.html", reports=[business.report(conn, b) for b in business.businesses(conn)])


# ---------------------------------------------------------------- recurring charges

@bp.route("/recurring")
def recurring_page():
    conn = get_db()
    r = recurring.report(conn)
    cadence = request.args.get("cadence", "")
    if cadence not in recurring.CADENCE_LABELS:
        cadence = ""
    shown = [i for i in r["active"] if not cadence or i["cadence"] == cadence]
    return render_template("recurring.html", r=r, shown=shown, cadence=cadence,
                           has_data=conn.execute("SELECT 1 FROM transactions LIMIT 1").fetchone() is not None)


@bp.post("/recurring/ignore")
def recurring_ignore():
    conn = get_db()
    key = request.form.get("key", "").strip()
    if key:
        recurring.ignore(conn, key, request.form.get("name", "").strip() or None)
        conn.commit()
        flash(f"Marked {request.form.get('name') or key} as not recurring.", "ok")
    return redirect(url_for(".recurring_page", cadence=request.form.get("cadence") or None))


@bp.post("/recurring/restore")
def recurring_restore():
    conn = get_db()
    key = request.form.get("key", "").strip()
    if key:
        recurring.restore(conn, key)
        conn.commit()
    return redirect(url_for(".recurring_page") + "#ignored")


# ---------------------------------------------------------------- bank sync (SimpleFIN Bridge)
# The browser's version of run.py's simplefin-* commands, on the same functions and the same access file.
# The Bridge allows about 24 requests a day and the daily sync uses one, so this page never calls it on
# its own: the account list is kept in settings, fetched only after connecting or on "Refresh accounts".

SF_LIST_KEY = "simplefin_account_list"        # {"fetched": "...", "accounts": [{id, org, label, balance}]}
SF_LAST_SYNC_KEY = "simplefin_last_web_sync"  # {"at": "...", "lines": [...]}: the last Sync now's summary
SF_LOG_LINES = 30
_URL_LOGIN = re.compile(r"://[^/\s@]+@")      # a user:password@ inside a URL is never shown


def _sf_data_dir():
    return Path(current_app.config["DATABASE"]).parent  # the folder run.py uses


def _sf_name(org, label):
    return f"{org} - {label}" if org else (label or "")


def _sf_error(e):
    return f"{e.get('msg')} [{e.get('code')}]" if isinstance(e, dict) else str(e)


def _sf_json(conn, key):
    try:
        return json.loads(budgeting.get_setting(conn, key, cast=str) or "null")
    except ValueError:
        return None


def _sf_refresh_list(conn, access_url):
    """One Bridge request. Returns the Bridge's notices; raises SimpleFinError."""
    accounts, errors = simplefin_import.list_accounts(access_url)
    budgeting.set_setting(conn, SF_LIST_KEY, json.dumps({
        "fetched": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "accounts": [{"id": i, "org": org, "label": label, "balance": balance} for i, org, label, balance in accounts],
    }))
    conn.commit()
    return errors


def _sf_hint(message):
    """The library's command-line advice, in this page's words."""
    return message.replace("run simplefin-setup with a new token", "disconnect, then connect with a new token")


def sync_report_lines(report):
    """What run.py simplefin-sync prints, line for line (account names instead of ids to map)."""
    lines = [f"{report['start']} through {report['end']} ({(report['end'] - report['start']).days + 1} days)"]
    names = [a["account"] for a in report["accounts"]]
    for a in report["accounts"]:
        name = f"{a['account']} [{a['label']}]" if names.count(a["account"]) > 1 else a["account"]
        balance = f", balance {a['balance']:,.2f}" if a["balance"] is not None else ""
        problem = "  (bank reported a problem: will retry these dates next run)" if not a["ok"] else ""
        lines.append(f"{name}: {a['added']} new of {a['read']}{balance}{problem}")
        if a.get("pending") or a.get("settled") or a.get("dropped"):
            lines.append(f"  pending from last month: {a['pending']} added, {a['settled']} posted and settled, "
                         f"{a['dropped']} never posted and dropped")
    lines += [f"Bridge says: {_sf_error(e)}" for e in report["errors"]]
    lines += [f"{n}: mapped, but the Bridge didn't return it (removed at bridge.simplefin.org?)" for n in report["missing"]]
    lines += [f"STALE: {s['account']} last pulled {s['last_synced'] or 'never'}" for s in report["stale"]]
    lines += [f"Not mapped yet: {_sf_name(org, label)}" for _, org, label in report["unmapped"]]
    return lines


@bp.route("/bank-sync")
def bank_sync():
    conn = get_db()
    data_dir = _sf_data_dir()
    status = {s["external_id"]: s for s in simplefin_import.status(conn)}
    cached = _sf_json(conn, SF_LIST_KEY) or {}
    rows = [{**a, "status": status.get(a["id"])} for a in cached.get("accounts") or []]
    listed = {a["id"] for a in rows}
    # Mapped but not in the saved list: mapped from the command line, or an old id after reconnecting a bank.
    # Only flagged once there is a saved list to be missing from.
    rows += [{"id": ext, "org": s["org"], "label": s["label"] or ext, "balance": None, "status": s,
              "not_listed": bool(cached.get("accounts"))}
             for ext, s in status.items() if ext not in listed]
    log = data_dir / "simplefin-sync.log"
    log_tail = log_updated = None
    # The log is written only by a scheduled sync (sync-mac.sh, cron, a timer); without one there is none.
    if log.exists():
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-SF_LOG_LINES:]
        log_tail = "\n".join(_URL_LOGIN.sub("://", line) for line in lines)
        log_updated = datetime.fromtimestamp(log.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    return render_template(
        "bank_sync.html", connected=simplefin_import.access_url_path(data_dir).exists(), rows=rows,
        mapped=list(status.values()), fetched=cached.get("fetched"),
        accounts=[a for a in _accounts(conn) if not a["closed"]], last_sync=_sf_json(conn, SF_LAST_SYNC_KEY),
        log_tail=log_tail, log_updated=log_updated, stale_days=simplefin_import.STALE_DAYS,
    )


@bp.post("/bank-sync/connect")
def bank_sync_connect():
    """Like run.py simplefin-setup: claim the token, save the access, list the accounts."""
    conn = get_db()
    token = (request.form.get("token") or "").strip()
    if not token:
        flash("Paste the setup token from bridge.simplefin.org.", "error")
        return redirect(url_for(".bank_sync"))
    try:
        access_url = simplefin_import.claim_setup_token(token)
    except simplefin_import.SimpleFinError as exc:
        flash(str(exc), "error")
        return redirect(url_for(".bank_sync"))
    simplefin_import.save_access_url(_sf_data_dir(), access_url)
    try:
        errors = _sf_refresh_list(conn, access_url)
    except simplefin_import.SimpleFinError as exc:
        flash(f"Connected, but the account list didn't come back: {_sf_hint(str(exc))} Try Refresh accounts later.", "error")
        return redirect(url_for(".bank_sync"))
    for e in errors:
        flash(f"Bridge says: {_sf_error(e)}", "error")
    flash("Connected. Pick where each account should sync to.", "ok")
    return redirect(url_for(".bank_sync"))


@bp.post("/bank-sync/refresh")
def bank_sync_refresh():
    conn = get_db()
    access_url = simplefin_import.load_access_url(_sf_data_dir())
    if not access_url:
        flash("Not connected yet.", "error")
        return redirect(url_for(".bank_sync"))
    try:
        errors = _sf_refresh_list(conn, access_url)
    except simplefin_import.SimpleFinError as exc:
        flash(_sf_hint(str(exc)), "error")
        return redirect(url_for(".bank_sync"))
    for e in errors:
        flash(f"Bridge says: {_sf_error(e)}", "error")
    flash("Refreshed the account list.", "ok")
    return redirect(url_for(".bank_sync"))


@bp.post("/bank-sync/map")
def bank_sync_map():
    """Like run.py simplefin-map: which account, transactions or balance only, and the day to start from."""
    conn = get_db()
    external_id = (request.form.get("external_id") or "").strip()
    aid = _int(request.form.get("account_id"))
    sync_from = _date_input(request.form.get("sync_from"))
    if not external_id:
        return redirect(url_for(".bank_sync"))
    existing = conn.execute("SELECT * FROM simplefin_accounts WHERE external_id = ?", (external_id,)).fetchone()
    if not aid:  # "Don't sync"
        if existing:
            conn.execute("DELETE FROM simplefin_accounts WHERE external_id = ?", (external_id,))
            conn.commit()
            flash("Stopped syncing that account. What it already brought in stays.", "ok")
        return redirect(url_for(".bank_sync"))
    row = conn.execute("SELECT id, name FROM accounts WHERE id = ?", (aid,)).fetchone()
    if not row:
        flash("Pick an account from the list.", "error")
        return redirect(url_for(".bank_sync"))
    transactions = {"txn": True, "balance": False}.get(request.form.get("mode"))  # else: by the account's type
    if existing and existing["account_id"] == aid:
        same_mode = transactions is None or bool(existing["transactions"]) == transactions
        if same_mode and (not sync_from or sync_from == existing["sync_from"]):
            flash(f"{row['name']}: nothing changed.", "ok")
            return redirect(url_for(".bank_sync"))
        sync_from = sync_from or existing["sync_from"]  # only the mode changed: keep its start
    transactions, sync_from = simplefin_import.map_account(conn, external_id, aid, transactions, sync_from)
    listed = next((a for a in (_sf_json(conn, SF_LIST_KEY) or {}).get("accounts") or [] if a["id"] == external_id), None)
    if listed:  # names for the status table until the next sync fills them in
        conn.execute("UPDATE simplefin_accounts SET org = COALESCE(org, ?), label = COALESCE(label, ?) WHERE external_id = ?",
                     (listed["org"], listed["label"], external_id))
    conn.commit()
    flash(f"{row['name']}: " + (f"transactions from {day_label(sync_from)} and the balance" if transactions else "balance only")
          + ", from the next sync.", "ok")
    return redirect(url_for(".bank_sync"))


@bp.post("/bank-sync/unmap")
def bank_sync_unmap():
    """Like run.py simplefin-unmap."""
    conn = get_db()
    gone = conn.execute("DELETE FROM simplefin_accounts WHERE external_id = ?", (request.form.get("external_id"),)).rowcount
    conn.commit()
    flash("Stopped syncing that account. What it already brought in stays." if gone else "That account wasn't syncing.", "ok")
    return redirect(url_for(".bank_sync"))


@bp.post("/bank-sync/sync")
def bank_sync_now():
    """Like run.py simplefin-sync: one Bridge request (more only when catching up a long gap)."""
    conn = get_db()
    access_url = simplefin_import.load_access_url(_sf_data_dir())
    if not access_url:
        flash("Not connected yet.", "error")
        return redirect(url_for(".bank_sync"))
    try:
        report = simplefin_import.sync(conn, access_url, date.today())
    except simplefin_import.SimpleFinError as exc:
        conn.rollback()
        flash(f"Sync failed, nothing changed: {_sf_hint(str(exc))}", "error")
        return redirect(url_for(".bank_sync"))
    conn.commit()
    budgeting.set_setting(conn, SF_LAST_SYNC_KEY, json.dumps(
        {"at": datetime.now().strftime("%Y-%m-%d %H:%M"), "lines": sync_report_lines(report)}))
    cached = _sf_json(conn, SF_LIST_KEY)
    if cached:  # accounts the sync came across that the saved list doesn't have yet
        known = {a["id"] for a in cached["accounts"]}
        cached["accounts"] += [{"id": i, "org": org, "label": label, "balance": None}
                               for i, org, label in report["unmapped"] if i not in known]
        budgeting.set_setting(conn, SF_LIST_KEY, json.dumps(cached))
    conn.commit()
    added = sum(a["added"] for a in report["accounts"])
    flash(f"Synced: {added} new transaction{'s' if added != 1 else ''}. Details below.", "ok")
    return redirect(url_for(".bank_sync") + "#last-sync")


@bp.post("/bank-sync/disconnect")
def bank_sync_disconnect():
    conn = get_db()
    simplefin_import.access_url_path(_sf_data_dir()).unlink(missing_ok=True)
    budgeting.set_setting(conn, SF_LIST_KEY, None)
    conn.commit()
    flash("Disconnected. Which account syncs where is kept, in case you connect again.", "ok")
    return redirect(url_for(".bank_sync"))
