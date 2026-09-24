"""Was it a good month, and why; what paychecks put away at work; and where the plan leaves you.

Spending is split three ways by each category's group (see seed.SPENDING_GROUPS):
  * fixed      = bills that don't move much month to month (mortgage, utilities, insurance)
  * flexible   = day-to-day spending you can steer (groceries, dining, shopping, hobbies...)
  * nonmonthly = lumpy costs (projects, travel, taxes)
A transaction marked one-off (a car, a big medical bill) is pulled out of its group, so a single
big purchase doesn't make an otherwise normal month look bad. It's still spending.

A good month is judged on flexible spending against the monthly target you set, because that's the
part you decide on together. Savings rate is total saved (cash kept plus what paychecks put into
retirement accounts, employer match included) over income including those contributions.
"""
import statistics
from datetime import date

from . import reports
from .balances import load_ledgers

GROUPS = ("fixed", "flexible", "nonmonthly")
BIG_CHARGE = 1000  # flexible or fixed charges this size are offered as one-offs
TYPICAL_MONTHS = 6


# ---------------------------------------------------------------- settings

def get_setting(conn, key, default=None, cast=float):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None or row[0] in (None, ""):
        return default
    try:
        return cast(row[0])
    except (TypeError, ValueError):
        return default


def set_setting(conn, key, value):
    if value is None:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    else:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )


# ---------------------------------------------------------------- paycheck savings

def contribution(row):
    """(yours, employer's) per paycheck for a paycheck_savings row."""
    yours = row["base_pay"] * row["employee_pct"] / 100
    employer = row["base_pay"] * min(row["employee_pct"], row["match_cap_pct"]) / 100 * row["match_rate"] / 100
    return yours, employer


def work_savings(conn, start, end, accounts=None):
    """What paychecks in [start, end) put into retirement accounts before reaching the bank.

    Each paycheck deposit matching a paycheck_savings entry uses the entry in effect on its date,
    so a raise or a new contribution rate is a new entry from that date on.
    """
    scope, extra = reports._scope(accounts)
    plans = conn.execute("SELECT * FROM paycheck_savings ORDER BY pattern, start_date").fetchall()
    yours = employer = 0.0
    paychecks = 0
    for pattern in {p["pattern"] for p in plans}:
        steps = [p for p in plans if p["pattern"] == pattern]
        rows = conn.execute(
            f"""SELECT t.date FROM transactions t WHERE t.raw_description LIKE ? AND t.amount > 0
                AND t.effective_date >= ? AND t.effective_date < ?{scope}""",
            (f"%{pattern}%", start, end, *extra),
        )
        for (day,) in rows:
            step = next((p for p in reversed(steps) if p["start_date"] <= day), None)
            if step is None:
                continue
            a, b = contribution(step)
            yours, employer, paychecks = yours + a, employer + b, paychecks + 1
    return {"yours": yours, "employer": employer, "total": yours + employer, "paychecks": paychecks}


# ---------------------------------------------------------------- the month scorecard

def _spending_rows(conn, start, end, accounts=None):
    scope, extra = reports._scope(accounts)
    return conn.execute(
        f"""SELECT c.id, COALESCE(c.name, 'Uncategorized') AS name, COALESCE(c.grp, 'flexible') AS grp,
                   t.one_off, -SUM(t.amount) AS total
            {reports.JOIN} WHERE t.effective_date >= ? AND t.effective_date < ? AND {reports.IS_SPEND}{scope}
            GROUP BY c.id, t.one_off""",
        (start, end, *extra),
    ).fetchall()


def by_group(conn, start, end, accounts=None):
    """{'fixed', 'flexible', 'nonmonthly', 'one_off'} spending, plus flexible spending per category."""
    out = dict.fromkeys((*GROUPS, "one_off"), 0.0)
    flexible = {}
    for r in _spending_rows(conn, start, end, accounts):
        if r["one_off"]:
            out["one_off"] += r["total"]
            continue
        out[r["grp"]] = out.get(r["grp"], 0.0) + r["total"]
        if r["grp"] == "flexible":
            flexible[(r["id"], r["name"])] = flexible.get((r["id"], r["name"]), 0.0) + r["total"]
    return out, flexible


def typical_flexible(conn, year, month, accounts=None, months=TYPICAL_MONTHS):
    """Median monthly flexible spending per category (one-offs left out) over the months before."""
    history = []
    for i in range(months, 0, -1):
        y, m = reports.add_months(year, month, -i)
        start, end = reports.bounds(y, m)
        if not conn.execute("SELECT 1 FROM transactions WHERE effective_date >= ? AND effective_date < ? LIMIT 1",
                            (start, end)).fetchone():
            continue
        groups, flexible = by_group(conn, start, end, accounts)
        history.append((groups["flexible"], flexible))
    if not history:
        return None, {}, 0
    keys = {k for _, f in history for k in f}
    per_category = {k: statistics.median(f.get(k, 0.0) for _, f in history) for k in keys}
    return statistics.median(total for total, _ in history), per_category, len(history)


def invested(conn, start, end, accounts=None):
    """Net money moved into investments (transfer categories in the 'saving' group)."""
    scope, extra = reports._scope(accounts)
    return conn.execute(
        f"""SELECT COALESCE(-SUM(t.amount), 0) {reports.JOIN}
            WHERE c.grp = 'saving' AND t.effective_date >= ? AND t.effective_date < ?{scope}""",
        (start, end, *extra),
    ).fetchone()[0]


def big_charges(conn, start, end, accounts=None):
    """Large fixed/flexible charges this period (marked one-off or not), biggest first."""
    scope, extra = reports._scope(accounts)
    return [dict(r) for r in conn.execute(
        f"""SELECT t.id, t.date, t.name, -t.amount AS amount, t.one_off, COALESCE(c.name, 'Uncategorized') AS category
            {reports.JOIN} WHERE t.effective_date >= ? AND t.effective_date < ? AND {reports.IS_SPEND}
            AND (t.one_off = 1 OR (-t.amount >= ? AND COALESCE(c.grp, 'flexible') IN ('fixed', 'flexible')
                                   AND COALESCE(c.snap_to_month, 0) = 0)){scope}
            ORDER BY t.amount""",
        (start, end, BIG_CHARGE, *extra),
    )]


def scorecard(conn, year, month, accounts=None):
    start, end = reports.bounds(year, month)
    totals = reports.totals(conn, start, end, accounts)
    groups, flexible = by_group(conn, start, end, accounts)
    typical_total, typical, typical_n = typical_flexible(conn, year, month, accounts)
    target = get_setting(conn, "flex_target")

    drivers = []
    for (cid, name), total in flexible.items():
        usual = typical.get((cid, name), 0.0)
        drivers.append({"id": cid, "name": name, "total": total, "typical": usual, "diff": total - usual})
    for (cid, name), usual in typical.items():
        if (cid, name) not in flexible and usual >= 1:
            drivers.append({"id": cid, "name": name, "total": 0.0, "typical": usual, "diff": -usual})
    over = sorted((d for d in drivers if d["diff"] >= 25), key=lambda d: -d["diff"])[:4]
    under = sorted((d for d in drivers if d["diff"] <= -25), key=lambda d: d["diff"])[:3]

    work = work_savings(conn, start, end, accounts)
    cash_flow = totals["income"] - totals["spending"]
    saved = cash_flow + work["total"]
    gross = totals["income"] + work["total"]
    return {
        "start": start, "end": end, **groups,
        "income": totals["income"], "spending": totals["spending"], "cash_flow": cash_flow,
        "target": target, "typical_flexible": typical_total, "typical_months": typical_n,
        "over": over, "under": under,
        "big": big_charges(conn, start, end, accounts),
        "invested": invested(conn, start, end, accounts),
        "work": work, "saved": saved, "rate": saved / gross if gross > 0 else None,
    }


def savings_rate(conn, start, end, accounts=None):
    totals = reports.totals(conn, start, end, accounts)
    work = work_savings(conn, start, end, accounts)
    saved = totals["income"] - totals["spending"] + work["total"]
    gross = totals["income"] + work["total"]
    return {"work": work["total"], "saved": saved, "rate": saved / gross if gross > 0 else None}


# ---------------------------------------------------------------- the plan

PLAN_KEYS = ("plan_income", "plan_fixed", "plan_nonmonthly", "flex_target", "plan_buffer")
BACKUP_KEYS = ("plan_backup_account", "plan_backup_account_2")  # drawn from in this order


def complete_months(conn, count, before=None):
    """The last `count` whole months before `before` (default: this month) that have data."""
    today = before or date.today()
    out, y, m = [], today.year, today.month
    for _ in range(count * 3):
        y, m = reports.add_months(y, m, -1)
        start, end = reports.bounds(y, m)
        if conn.execute("SELECT 1 FROM transactions WHERE effective_date >= ? AND effective_date < ? LIMIT 1",
                        (start, end)).fetchone():
            out.append((y, m))
        if len(out) == count:
            break
    return sorted(out)


def baseline(conn, months):
    """Monthly averages over `months` [(year, month)] to start a plan from.

    Income is listed by source so leave pay, refunds and one-time deposits can be left out;
    non-monthly spending is averaged over the 12 months before the last one, since it's lumpy.
    """
    if not months:
        return None
    n = len(months)
    start, end = reports.bounds(*months[0])[0], reports.bounds(*months[-1])[1]
    sources = [dict(r) for r in conn.execute(
        f"""SELECT CASE WHEN c.name = 'Paycheck' THEN t.name ELSE c.name END AS name, c.name AS category,
                   SUM(t.amount) / ? AS monthly
            {reports.JOIN} WHERE {reports.IS_INCOME} AND t.effective_date >= ? AND t.effective_date < ?
            GROUP BY 1 ORDER BY monthly DESC""",
        (n, start, end),
    )]
    groups = dict.fromkeys((*GROUPS, "one_off"), 0.0)
    for y, m in months:
        g, _ = by_group(conn, *reports.bounds(y, m))
        for k in groups:
            groups[k] += g[k] / n
    ly, lm = months[-1]
    year_start = reports.bounds(*reports.add_months(ly, lm, -11))[0]
    year_groups, _ = by_group(conn, year_start, end)
    return {
        "months": months, "sources": sources,
        "regular_income": sum(s["monthly"] for s in sources if s["category"] in ("Paycheck", "Rental Income", "Side Income")),
        "fixed": groups["fixed"], "flexible": groups["flexible"], "nonmonthly": groups["nonmonthly"],
        "one_off": groups["one_off"], "nonmonthly_year": year_groups["nonmonthly"] / 12,
    }


def project(conn, months=12, today=None):
    """Month-by-month projection of the plan account from next month on.

    Each month: planned income - fixed - flexible target - non-monthly + plan items. When the plan
    account would drop below the buffer, the difference is drawn from the backup accounts in order
    (BACKUP_KEYS): the first until it's empty, then the next.
    """
    today = today or date.today()
    values = {k: get_setting(conn, k) for k in PLAN_KEYS}
    if values["plan_income"] is None:
        return None
    ledgers = load_ledgers(conn)
    account = get_setting(conn, "plan_account", cast=int)
    iso = today.isoformat()
    backups = [b for b in (get_setting(conn, k, cast=int) for k in BACKUP_KEYS) if b in ledgers]
    balance = ledgers[account].on(iso) if account in ledgers else 0.0
    left = {b: ledgers[b].on(iso) for b in backups}
    buffer = values["plan_buffer"] or 0.0
    items = [dict(r) for r in conn.execute("SELECT * FROM plan_items ORDER BY start_month, id")]

    rows, drawn = [], {b: 0.0 for b in backups}
    y, m = today.year, today.month
    start_balance, start_left = balance, dict(left)
    for _ in range(months):
        y, m = reports.add_months(y, m, 1)
        key = f"{y}-{m:02d}"
        active = [i for i in items if i["start_month"] <= key and (i["end_month"] or "9999-12") >= key]
        adjust = sum(i["amount"] for i in active)
        spend = (values["plan_fixed"] or 0) + (values["flex_target"] or 0) + (values["plan_nonmonthly"] or 0)
        net = values["plan_income"] + adjust - spend
        balance += net
        draws = {}
        for b in backups:
            if balance >= buffer:
                break
            take = min(buffer - balance, max(left[b], 0.0))
            if take > 0:
                draws[b] = take
                balance += take
                left[b] -= take
                drawn[b] += take
        draw, reserve = sum(draws.values()), sum(left.values())
        rows.append({
            "key": key, "income": values["plan_income"] + sum(i["amount"] for i in active if i["amount"] > 0),
            "adjust": adjust, "items": active, "spend": spend, "net": net, "balance": balance,
            "draw": draw, "draws": draws, "reserve": reserve, "left": dict(left), "short": balance < buffer - 0.005,
        })
    return {
        "values": values, "items": items, "rows": rows, "start_balance": start_balance, "start_left": start_left,
        "account": account, "backups": backups, "drawn": drawn, "total_drawn": sum(drawn.values()),
        "average_net": sum(r["net"] for r in rows) / len(rows) if rows else 0.0,
        "first_draw": next((r["key"] for r in rows if r["draw"] > 0), None),
        "first_short": next((r["key"] for r in rows if r["short"]), None),
    }
