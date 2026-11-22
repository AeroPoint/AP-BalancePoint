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
import re
import statistics
from datetime import date, timedelta

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
    target = flex_target(conn)

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
        "savings_first": plan_values(conn)["mode"] == "savings",
        "over": over, "under": under,
        "big": big_charges(conn, start, end, accounts),
        "invested": invested(conn, start, end, accounts),
        "work": work, "saved": saved, "rate": saved / gross if gross > 0 else None,
    }


PACE_COUNTS_PENDING = "pace_counts_pending"  # "0" turns it off; unset means on
SHOW_PENDING = "show_pending"  # "0" hides this month's pending charges on Transactions; unset means shown


def setting_on(conn, key):
    """A yes/no household setting that's on unless it was turned off."""
    return get_setting(conn, key, cast=str) != "0"


def pending_now(conn, today):
    """This month's still-pending charges from the last bank sync, newest first ([] if none or another month)."""
    import json
    raw = conn.execute("SELECT value FROM settings WHERE key = 'pending_this_month'").fetchone()
    try:
        snap = json.loads(raw[0]) if raw else None
    except (TypeError, ValueError):
        return []
    if not snap or snap.get("month") != today.isoformat()[:7]:
        return []
    return sorted(snap.get("items", []), key=lambda i: i.get("date") or "", reverse=True)


def pending_by_group(conn, today):
    """This month's still-pending spending per group ({"fixed", "flexible"}), from the last bank sync's
    snapshot. Uncategorized money out counts as flexible."""
    out = {"fixed": 0.0, "flexible": 0.0}
    if not setting_on(conn, PACE_COUNTS_PENDING):
        return out
    cats = {r["id"]: r for r in conn.execute("SELECT id, kind, COALESCE(grp, 'flexible') AS grp FROM categories")}
    for item in pending_now(conn, today):
        c = cats.get(item.get("category_id"))
        if c is None and item["amount"] < 0:
            out["flexible"] -= item["amount"]
        elif c and c["kind"] == "expense" and c["grp"] in out:
            out[c["grp"]] -= item["amount"]
    return {k: round(v, 2) for k, v in out.items()}


def pending_flexible(conn, today):
    """Flexible spending in this month's still-pending charges, from the last bank sync's snapshot."""
    return pending_by_group(conn, today)["flexible"]


FORECAST_GROUPS = (("flexible", "flex_target", "Flexible"), ("fixed", "plan_fixed", "Fixed"))
EXPECTED_MATCH = 0.15  # a charge this close to an expected one (and from it) is that charge


def _words(text):
    return {w for w in re.findall(r"[a-z]+", (text or "").lower()) if len(w) >= 4}


def expected_this_month(conn, today, pending=()):
    """The plan's expected changes that are spending this month ({"label", "amount", "category", "grp",
    "made"}); "made" when a charge this month (posted or pending) is within 15% of it and is its category
    or shares a word with its name. grp is None for spending on top of the targets (no category, or a
    non-monthly one)."""
    key = today.strftime("%Y-%m")
    start, end = reports.bounds(today.year, today.month)
    cats = {r["id"]: r for r in conn.execute("SELECT id, name, COALESCE(grp, 'flexible') AS grp FROM categories")}
    charges = [(r["category_id"], r["name"], -r["amount"]) for r in conn.execute(
        f"""SELECT t.category_id, t.name, COALESCE(t.split_total, t.amount) AS amount {reports.JOIN}
            WHERE t.split_of IS NULL AND COALESCE(t.split_total, t.amount) < 0 AND t.date >= ? AND t.date < ?""",
        (start, end))]
    charges += [(p.get("category_id"), p.get("name"), -p["amount"]) for p in pending if p.get("amount", 0) < 0]
    out, used = [], set()
    for i in conn.execute("""SELECT * FROM plan_items WHERE amount < 0 AND start_month <= ?
                             AND COALESCE(end_month, '9999-12') >= ? ORDER BY id""", (key, key)):
        amount = -i["amount"]
        c = cats.get(i["category_id"])
        made = next((n for n, (cid, name, paid) in enumerate(charges) if n not in used
                     and abs(paid - amount) <= EXPECTED_MATCH * amount
                     and ((i["category_id"] and cid == i["category_id"]) or _words(i["label"]) & _words(name))), None)
        if made is not None:
            used.add(made)
        out.append({"label": i["label"], "amount": amount, "category": c["name"] if c else None,
                    "grp": c["grp"] if c and c["grp"] in ("fixed", "flexible") else None, "made": made is not None})
    return out


def forecast(conn, today=None):
    """Where this month's flexible and fixed spending is headed, against the plan's targets: spent so far
    (pending charges included) plus what's known to still be coming, the regular charges from the
    Recurring page and the expected changes entered on the Plan page. Nothing else is guessed, so the
    flexible money left is shared over the days left (a day's allowance), and it warns when a forecast
    passes its target or the allowance drops well below the plan's day. Also the plan account and backups against
    their minimums. None without a target or plan.

    {"day", "days", "days_left", "groups": {"flexible"|"fixed": {"label", "target", "spent", "pending",
     "upcoming", "forecast", "left"}}, "coming": [{"name", "amount", "date", "grp", "kind"}],
     "outside": [expected spending on top of the targets], "per_day_left", "planned_per_day",
     "usual_per_day" (from recent months), "warnings",
     and the flexible ones again as "target", "spent", "pending", "forecast"}
    """
    from . import recurring

    today = today or date.today()
    start, end = reports.bounds(today.year, today.month)
    days = (date.fromisoformat(end) - date.fromisoformat(start)).days
    days_left = days - today.day + 1
    targets = {g: (flex_target(conn) if key == "flex_target" else get_setting(conn, key)) for g, key, _ in FORECAST_GROUPS}
    out = {"day": today.day, "days": days, "days_left": days_left, "warnings": [], "spent": None, "target": targets["flexible"]}
    if any(targets.values()):
        grp_of = {r["name"]: r["grp"] for r in conn.execute("SELECT name, COALESCE(grp, 'flexible') AS grp FROM categories WHERE kind = 'expense'")}
        pending_items = pending_now(conn, today) if setting_on(conn, PACE_COUNTS_PENDING) else []
        pending = pending_by_group(conn, today)
        spent = by_group(conn, start, (today + timedelta(days=1)).isoformat())[0]
        coming = []
        for r in recurring.still_due(conn, today, pending_items):
            grp = grp_of.get(r["category"], "flexible") if r["category"] else "flexible"
            if grp in targets:
                coming.append({**r, "grp": grp, "kind": "recurring"})
        expected = expected_this_month(conn, today, pending_items)
        coming += [{"name": e["label"], "amount": e["amount"], "date": None, "grp": e["grp"], "kind": "expected"}
                   for e in expected if e["grp"] and not e["made"]]
        out["coming"] = sorted(coming, key=lambda c: (c["date"] or "9999", c["name"]))
        out["outside"] = [e for e in expected if not e["grp"]]
        groups = {}
        for g, _, label in FORECAST_GROUPS:
            upcoming = sum(c["amount"] for c in coming if c["grp"] == g)
            total = spent[g] + pending[g] + upcoming
            groups[g] = {"label": label, "target": targets[g], "spent": spent[g] + pending[g], "pending": pending[g],
                         "upcoming": upcoming, "forecast": total,
                         "left": targets[g] - total if targets[g] else None}
            if targets[g] and total > targets[g]:
                out["warnings"].append(f"{label} spending is on track for ${total:,.0f} this month, "
                                       f"${total - targets[g]:,.0f} over the ${targets[g]:,.0f} target.")
        out["groups"] = groups
        flex = groups["flexible"]
        out.update(spent=flex["spent"], pending=flex["pending"], forecast=flex["forecast"], per_day_left=None,
                   planned_per_day=None, usual_per_day=None)
        if targets["flexible"]:
            # A day's allowance against the plan's: the target less the regular flexible charges, over the month.
            regular = sum(i["monthly"] for i in recurring.report(conn, today)["active"]
                          if grp_of.get(i["category"], "flexible") == "flexible")
            out["per_day_left"] = flex["left"] / days_left
            out["planned_per_day"] = max(targets["flexible"] - regular, 0.0) / days
            usual, _, months = typical_flexible(conn, today.year, today.month)
            if usual and months:
                out["usual_per_day"] = max(usual - regular, 0.0) / days
            short = (out["planned_per_day"] - out["per_day_left"]) * days_left
            if flex["left"] >= 0 and short > max(50, 0.1 * targets["flexible"]):
                last = date.fromisoformat(end) - timedelta(days=1)
                out["warnings"].append(f"${out['per_day_left']:,.0f} a day is left for flexible spending through "
                                       f"{last:%b} {last.day}, under the plan's ${out['planned_per_day']:,.0f} a day.")
    account, cushion = get_setting(conn, "plan_account", cast=int), get_setting(conn, "plan_buffer")
    if account and cushion:
        ledger = load_ledgers(conn).get(account)
        balance = ledger.on(today.isoformat()) if ledger else None
        if balance is not None:
            out.update(account=ledger.account["name"], balance=balance, cushion=cushion)
            if balance < cushion:
                out["warnings"].append(f"{ledger.account['name']} is at ${balance:,.0f}, ${cushion - balance:,.0f} below "
                                       f"its ${cushion:,.0f} cushion.")
    ledgers = None
    for key, min_key in zip(BACKUP_KEYS, BACKUP_MIN_KEYS):
        backup, floor = get_setting(conn, key, cast=int), get_setting(conn, min_key)
        if backup and floor:
            ledgers = ledgers or load_ledgers(conn)
            ledger = ledgers.get(backup)
            bal = ledger.on(today.isoformat()) if ledger else None
            if bal is not None and bal < floor:
                out["warnings"].append(f"{ledger.account['name']} is at ${bal:,.0f}, ${floor - bal:,.0f} below the "
                                       f"${floor:,.0f} the plan keeps in it.")
    return out if any(targets.values()) or out["warnings"] else None


def savings_rate(conn, start, end, accounts=None):
    totals = reports.totals(conn, start, end, accounts)
    work = work_savings(conn, start, end, accounts)
    saved = totals["income"] - totals["spending"] + work["total"]
    gross = totals["income"] + work["total"]
    return {"work": work["total"], "saved": saved, "rate": saved / gross if gross > 0 else None}


# ---------------------------------------------------------------- the plan

PLAN_KEYS = ("plan_income", "plan_fixed", "plan_nonmonthly", "flex_target", "plan_buffer")
BACKUP_KEYS = ("plan_backup_account", "plan_backup_account_2")  # drawn from in this order
BACKUP_MIN_KEYS = ("plan_backup_min", "plan_backup_min_2")  # each backup's own "keep at least" (blank = 0)
# Two ways to plan a month. "flex" (the default): set the flexible target, and saving is whatever's left.
# "savings": set a savings goal (an amount, or a % of income, put into an account each month), and the
# flexible target is what's left after fixed, non-monthly and the goal.
PLAN_MODE = "plan_mode"
PLAN_THROUGH = "plan_through"  # "YYYY-MM", the plan's last month; unset = always the next 12 months
PLAN_MAX_MONTHS = 40 * 12
SAVINGS_KEYS = ("plan_savings", "plan_savings_unit", "plan_savings_account")  # amount or %, "$" or "%", account id


def plan_values(conn):
    """The plan's monthly numbers, with the flexible target worked out from the savings goal when planning
    savings first. Adds mode, savings (per month), savings_goal, savings_unit and savings_account."""
    v = {k: get_setting(conn, k) for k in PLAN_KEYS}
    goal = get_setting(conn, "plan_savings")
    unit = "%" if get_setting(conn, "plan_savings_unit", cast=str) == "%" else "$"
    account = get_setting(conn, "plan_savings_account", cast=int)
    savings_first = get_setting(conn, PLAN_MODE, cast=str) == "savings"
    savings = 0.0
    if savings_first and goal is not None and v["plan_income"] is not None:
        savings = round(goal * v["plan_income"] / 100 if unit == "%" else goal, 2)
        v["flex_target"] = round(v["plan_income"] - (v["plan_fixed"] or 0) - (v["plan_nonmonthly"] or 0) - savings, 2)
    else:
        savings_first = False
    return {**v, "mode": "savings" if savings_first else "flex", "savings": savings, "savings_goal": goal,
            "savings_unit": unit, "savings_account": account}


def plan_end(conn, today=None):
    """(last month "YYYY-MM", months from now, set by hand?) for the plan: the month set on the Plan page
    (up to 40 years out), else 12 months from now."""
    today = today or date.today()
    saved = get_setting(conn, PLAN_THROUGH, cast=str) or ""
    if len(saved) == 7 and saved[4] == "-" and saved[:4].isdigit() and saved[5:].isdigit():
        months = (int(saved[:4]) - today.year) * 12 + int(saved[5:]) - today.month
        if 1 <= months:
            months = min(months, PLAN_MAX_MONTHS)
            y, m = reports.add_months(today.year, today.month, months)
            return f"{y}-{m:02d}", months, True
    y, m = reports.add_months(today.year, today.month, 12)
    return f"{y}-{m:02d}", 12, False


def flex_target(conn):
    """The flexible target the month is judged on: as set, or what the savings goal leaves (never below 0)."""
    target = plan_values(conn)["flex_target"]
    return None if target is None else max(target, 0.0)


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


def plan_items(conn):
    """Plan changes that move the plan. One with a fixed or flexible category is spending inside that
    month's target (the month forecast counts it there), so only the others count."""
    return [dict(r) for r in conn.execute(
        """SELECT p.* FROM plan_items p LEFT JOIN categories c ON c.id = p.category_id
           WHERE p.amount > 0 OR c.id IS NULL OR COALESCE(c.grp, 'flexible') NOT IN ('fixed', 'flexible')
           ORDER BY p.start_month, p.id""")]


def plan_month(values, items, key):
    """(changes active, their total, planned spending, net into the plan account) for a month "YYYY-MM".
    Planning savings first, the savings goal is taken out of the net (it goes to its own account)."""
    active = [i for i in items if i["start_month"] <= key and (i["end_month"] or "9999-12") >= key]
    adjust = sum(i["amount"] for i in active)
    spend = (values["plan_fixed"] or 0) + max(values["flex_target"] or 0, 0) + (values["plan_nonmonthly"] or 0)
    saving = values["savings"] if values["mode"] == "savings" else 0.0
    return active, adjust, spend, values["plan_income"] + adjust - spend - saving


def project(conn, months=None, today=None):
    """Month-by-month projection of the plan account from next month through the plan's end (plan_end).

    Each month: planned income - fixed - flexible target - non-monthly (- the savings goal, planning
    savings first) + plan items. The savings goal goes into its account. When the plan account would drop
    below the buffer, the difference is drawn from the backup accounts in order (BACKUP_KEYS): the first
    down to its own minimum (BACKUP_MIN_KEYS; blank = empty), then the next.
    """
    today = today or date.today()
    values = plan_values(conn)
    end_key, end_months, end_set = plan_end(conn, today)
    months = months or end_months
    if values["plan_income"] is None:
        return None
    ledgers = load_ledgers(conn)
    account = get_setting(conn, "plan_account", cast=int)
    iso = today.isoformat()
    chosen = [(get_setting(conn, k, cast=int), get_setting(conn, mk) or 0.0) for k, mk in zip(BACKUP_KEYS, BACKUP_MIN_KEYS)]
    backups = [b for b, _ in chosen if b in ledgers]
    floors = {b: f for b, f in chosen if b in ledgers}
    balance = (ledgers[account].on(iso) or 0.0) if account in ledgers else 0.0
    left = {b: ledgers[b].on(iso) or 0.0 for b in backups}
    buffer = values["plan_buffer"] or 0.0
    items = plan_items(conn)

    saving = values["savings"] if values["mode"] == "savings" else 0.0
    save_to = values["savings_account"]
    saved_total = 0.0
    rows, drawn = [], {b: 0.0 for b in backups}
    y, m = today.year, today.month
    start_balance, start_left = balance, dict(left)
    for _ in range(months):
        y, m = reports.add_months(y, m, 1)
        key = f"{y}-{m:02d}"
        active, adjust, spend, net = plan_month(values, items, key)
        balance += net
        saved_total += saving
        if save_to in left:
            left[save_to] += saving
        draws = {}
        for b in backups:
            if balance >= buffer:
                break
            take = min(buffer - balance, max(left[b] - floors[b], 0.0))
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
            "saved": saving,
        })
    return {
        "values": values, "items": items, "rows": rows, "start_balance": start_balance, "start_left": start_left,
        "account": account, "backups": backups, "floors": floors, "drawn": drawn, "total_drawn": sum(drawn.values()),
        "saving": saving, "save_to": save_to, "saved_total": saved_total, "end": rows[-1]["key"] if rows else end_key,
        "end_set": end_set, "months": months,
        "average_net": sum(r["net"] for r in rows) / len(rows) if rows else 0.0,
        "first_draw": next((r["key"] for r in rows if r["draw"] > 0), None),
        "first_short": next((r["key"] for r in rows if r["short"]), None),
    }
