"""Pages and small JSON endpoints."""
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from . import personal, reports, seed
from .csv_import import CsvFormatError, parse_csv, store_transactions
from .db import get_db
from .excel_import import ensure_account, import_workbook
from .rules import RuleEngine, compile_pattern, merchant_key, normalize, reapply

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
    existing = conn.execute("SELECT id FROM rules WHERE match_on = ? AND pattern = ?", (match_on, pattern)).fetchone()
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
    if RuleEngine(conn).resolve(txn["raw_description"], txn["mcc"]).name != new_name:
        upsert_rule(conn, "raw", merchant_key(txn["raw_description"]), rename_to=new_name)
    changed = conn.execute("UPDATE transactions SET name = ? WHERE name = ?", (new_name, old)).rowcount
    return changed + reapply(conn, "name_locked = 0")


def _txn_json(conn, tid):
    row = conn.execute(
        """SELECT t.id, t.name, t.category_id, t.category_source, t.notes, c.name AS category_name
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


@bp.app_context_processor
def inject_nav():
    count = get_db().execute("SELECT COUNT(*) FROM transactions WHERE category_id IS NULL").fetchone()[0]
    return {"uncategorized_count": count, "endpoint": request.endpoint}


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
    return render_template(
        "dashboard.html",
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
        t["year"] = y
        yearly.append(t)
    return render_template(
        "trends.html",
        year=year, years=all_years or [year], months=MONTHS, acct=acct, accounts=_accounts(conn),
        matrix=matrix, month_totals=month_totals, active=active,
        cumulative=reports.cumulative_spending(conn, compare, acct),
        stacked=reports.stacked_by_category(conn, year, accounts=acct),
        flow=reports.monthly(conn, year, 1, 12, acct), yearly=yearly,
    )


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
        where.append("t.date >= ? AND t.date < ?")
        params += [start, end]
    if args.get("source") in ("mcc", "manual", "sheet", "rule", "none"):
        where.append("t.category_source = ?")
        params.append(args["source"])
    if args.get("name"):
        where.append("t.name = ?")
        params.append(args["name"])

    clause = " AND ".join(where)
    page = max(_int(args.get("page"), 1), 1)
    summary = conn.execute(
        f"SELECT COUNT(*) AS n, COALESCE(SUM(t.amount), 0) AS total FROM transactions t WHERE {clause}", params
    ).fetchone()
    rows = conn.execute(
        f"""SELECT t.*, a.name AS account_name, c.name AS category_name
            FROM transactions t JOIN accounts a ON a.id = t.account_id
            LEFT JOIN categories c ON c.id = t.category_id
            WHERE {clause} ORDER BY t.date DESC, t.id DESC LIMIT ? OFFSET ?""",
        (*params, PER_PAGE, (page - 1) * PER_PAGE),
    ).fetchall()
    periods = [r[0] for r in conn.execute("SELECT DISTINCT substr(date, 1, 7) FROM transactions ORDER BY 1 DESC")]
    return render_template(
        "transactions.html",
        rows=rows, summary=summary, page=page, pages=max(1, -(-summary["n"] // PER_PAGE)),
        categories=_categories(conn), accounts=_accounts(conn),
        periods=periods, years=sorted({p[:4] for p in periods}, reverse=True),
    )


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

    if "notes" in data:
        conn.execute("UPDATE transactions SET notes = ? WHERE id = ?", (str(data["notes"]).strip() or None, tid))

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
                "UPDATE rules SET pattern = ?, rename_to = ?, category_id = ?, source = 'user' WHERE id = ?",
                (pattern, rename_to, category_id, rid),
            )
        else:
            conn.execute(
                "INSERT INTO rules (match_on, pattern, rename_to, category_id, source) VALUES (?, ?, ?, ?, 'user')",
                (match_on, pattern, rename_to, category_id),
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
    return render_template("categories.html", rows=rows)


@bp.post("/categories/save")
def save_category():
    conn = get_db()
    name = " ".join(request.form.get("name", "").split())
    kind = request.form.get("kind")
    if not name or kind not in ("income", "expense", "transfer"):
        flash("A category needs a name and a type.", "error")
        return redirect(url_for(".categories_page"))
    cid = _int(request.form.get("id"))
    try:
        if cid:
            conn.execute("UPDATE categories SET name = ?, kind = ? WHERE id = ?", (name, kind, cid))
        else:
            conn.execute("INSERT INTO categories (name, kind, sort) VALUES (?, ?, 999)", (name, kind))
        conn.commit()
        flash(f"Saved category “{name}”.", "ok")
    except sqlite3.IntegrityError:
        flash(f"There's already a category named “{name}”.", "error")
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
    accounts = []
    for r in rows:
        a = dict(r)
        activity = max(filter(None, [(r["last_date"] or "")[:7], r["last_nonzero"]]), default=None)
        # Suggest closing accounts that have gone quiet, but never close anything on our own.
        a["quiet_since"] = activity if (
            not r["closed"] and activity and latest and _months_between(activity, latest) >= 4
        ) else None
        accounts.append(a)
    return render_template("accounts.html", accounts=accounts, kinds=seed.ACCOUNT_KINDS, groups=seed.ACCOUNT_GROUPS)


@bp.post("/accounts/save")
def save_account():
    conn = get_db()
    form = request.form
    name = " ".join(form.get("name", "").split())
    kind = form.get("kind") if form.get("kind") in seed.KIND else "other"
    institution = " ".join(form.get("institution", "").split()) or None
    opened, closed = _month_input(form.get("opened")), _month_input(form.get("closed"))
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
                "UPDATE accounts SET name = ?, kind = ?, institution = ?, opened = ?, closed = ? WHERE id = ?",
                (name, kind, institution, opened, closed, aid),
            )
        else:
            conn.execute(
                "INSERT INTO accounts (name, kind, institution, opened, closed) VALUES (?, ?, ?, ?, ?)",
                (name, kind, institution, opened, closed),
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
    moved = conn.execute(
        "UPDATE OR IGNORE transactions SET account_id = ?, dedupe_key = ? || substr(dedupe_key, instr(dedupe_key, '|')) "
        "WHERE account_id = ?",
        (target, str(target), aid),
    ).rowcount
    duplicates = conn.execute("DELETE FROM transactions WHERE account_id = ?", (aid,)).rowcount
    conn.execute(
        "INSERT OR IGNORE INTO balances (account_id, month, amount, source) "
        "SELECT ?, month, amount, source FROM balances WHERE account_id = ?",
        (target, aid),
    )
    conn.execute("UPDATE imports SET account_id = ? WHERE account_id = ?", (target, aid))
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
    month = _month_input(request.values.get("month")) or date.today().strftime("%Y-%m")
    accounts = []
    for row in _accounts(conn):
        a = dict(row)
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
                cleared += conn.execute(
                    "DELETE FROM balances WHERE account_id = ? AND month = ?", (a["id"], month)
                ).rowcount
            else:
                conn.execute(
                    """INSERT INTO balances (account_id, month, amount, source) VALUES (?, ?, ?, 'manual')
                       ON CONFLICT (account_id, month) DO UPDATE SET amount = excluded.amount, source = 'manual'""",
                    (a["id"], month, value),
                )
                saved += 1
        conn.commit()
        flash(
            f"Saved {saved} balance{'s' if saved != 1 else ''} for {month_label(month)}."
            + (f" Cleared {cleared}." if cleared else ""),
            "ok",
        )
        return redirect(url_for(".net_worth", month=month))

    balances = conn.execute("SELECT account_id, month, amount FROM balances ORDER BY month").fetchall()
    current = {r["account_id"]: r["amount"] for r in balances if r["month"] == month}
    previous = {}
    for r in balances:
        if r["month"] < month:
            previous[r["account_id"]] = (r["amount"], r["month"])
    year, mon = int(month[:4]), int(month[5:])
    py, pm = reports.add_months(year, mon, -1)
    ny, nm = reports.add_months(year, mon, 1)
    payload = {
        "month": month,
        "today": date.today().strftime("%Y-%m"),
        "accounts": [{k: a[k] for k in ("id", "name", "kind", "side", "group", "opened", "closed")} for a in accounts],
        "balances": [[r["account_id"], r["month"], r["amount"]] for r in balances],
    }
    return render_template(
        "net_worth.html",
        month=month, accounts=accounts, open_accounts=open_accounts, groups=seed.ACCOUNT_GROUPS,
        current=current, previous=previous, payload=payload,
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
                parsed = parse_csv(text)
            except CsvFormatError as exc:
                flash(f"{f.filename}: {exc}", "error")
                continue
            _, read, added, _ = store_transactions(conn, account_id, f.filename, parsed)
            flash(f"{f.filename}: added {added} of {read} transactions ({read - added} were already imported).", "ok")
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
        f"{result['recategorized']:,} of them changed. {result['balances']:,} month-end balances.",
        "ok",
    )
    return redirect(url_for(".upload"))


@bp.post("/imports/<int:iid>/delete")
def delete_import(iid):
    conn = get_db()
    removed = conn.execute("SELECT COUNT(*) FROM transactions WHERE import_id = ?", (iid,)).fetchone()[0]
    conn.execute("DELETE FROM imports WHERE id = ?", (iid,))
    conn.commit()
    flash(f"Removed that import and its {removed} transactions.", "ok")
    return redirect(url_for(".upload"))
