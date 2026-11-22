"""Projection page: where your accounts could be in 1 to 40 years.

Each account grows at a yearly rate, with a volatility (how much a year swings) for the likely range.
Rates start from the account's own balance history when it has 3+ years of it (growth after the money
known to have gone in, like paycheck 401(k) contributions), else from a default for the kind of account;
either can be changed, per account or one rate for every investment. Paycheck contributions continue,
loans with terms pay down on schedule, and the Plan's months can run through it: each month's surplus
or shortfall lands in the plan account, draws from the backups as on the Plan page, and the savings goal
goes into its account. After the Plan's last month (Plan page, "Plan through") the planned month carries
on as it is now, without the changes.

The expected line uses each rate exactly. The range comes from simulated runs (a fixed seed, so the page
doesn't change on reload): every month draws one market move that all the investments share, scaled by
each one's volatility, which is the cautious assumption (they fall together). The band is the 10th to
90th percentile of net worth across runs. Optionally in today's dollars, after inflation.
"""
import json
import math
import random
import statistics
from datetime import date

from . import budgeting, reports, seed
from .balances import load_ledgers, loan_balance

SETTING = "projection"
RUNS = 300
SEED = 20261122
MIN_HISTORY = 12  # months of balances before an account's history is worked out at all
TRUSTED_HISTORY = 36  # ...and before it sets the rate: a year or two mostly measures that stretch's market
INVESTMENT_KINDS = ("brokerage", "retirement", "hsa", "crypto")
# kind: (yearly rate %, volatility %, on by default)
DEFAULTS = {
    "checking": (0.0, 0.0, True), "savings": (2.0, 0.0, True), "cash": (0.0, 0.0, True),
    "brokerage": (7.0, 15.0, True), "retirement": (7.0, 15.0, True), "hsa": (5.0, 10.0, True),
    "crypto": (5.0, 60.0, True), "property": (3.0, 5.0, True), "vehicle": (-15.0, 0.0, True),
    "other": (0.0, 0.0, True), "credit": (0.0, 0.0, True), "loan": (0.0, 0.0, True),
}
PLAUSIBLE = (-10.0, 25.0)  # a history outside this (yearly %) is more likely deposits than growth
JUMP = 0.15  # a month that moved more than this is taken as money moved in or out (unrecorded), not growth


def load_setup(conn):
    try:
        saved = json.loads(budgeting.get_setting(conn, SETTING, cast=str) or "{}")
    except ValueError:
        saved = {}
    return saved if isinstance(saved, dict) else {}


def save_setup(conn, setup):
    budgeting.set_setting(conn, SETTING, json.dumps(setup, sort_keys=True))


def _month_key(y, m):
    return f"{y}-{m:02d}"


def contributions(conn, start, end):
    """{account id: {"YYYY-MM": amount}} that paychecks put into accounts (yours plus the match)."""
    out = {}
    plans = conn.execute("SELECT * FROM paycheck_savings WHERE account_id IS NOT NULL ORDER BY pattern, start_date").fetchall()
    for pattern in {p["pattern"] for p in plans}:
        steps = [p for p in plans if p["pattern"] == pattern]
        for (day,) in conn.execute(
                "SELECT date FROM transactions WHERE raw_description LIKE ? AND amount > 0 AND effective_date >= ? AND effective_date < ?",
                (f"%{pattern}%", start, end)):
            step = next((p for p in reversed(steps) if p["start_date"] <= day), None)
            if step is None:
                continue
            yours, employer = budgeting.contribution(step)
            months = out.setdefault(step["account_id"], {})
            months[day[:7]] = months.get(day[:7], 0.0) + yours + employer
    return out


def history_rate(conn, account_id, flows):
    """(yearly rate %, volatility %, months used) from consecutive month-end balances, net of known money
    put in (paycheck contributions and the account's own deposits). Months that jumped more than JUMP are
    left out as unrecorded deposits or withdrawals. None with under MIN_HISTORY months left."""
    rows = conn.execute("SELECT month, amount FROM balances WHERE account_id = ? ORDER BY month", (account_id,)).fetchall()
    own = dict(conn.execute(
        "SELECT substr(date, 1, 7), SUM(amount) FROM transactions WHERE account_id = ? GROUP BY 1", (account_id,)).fetchall())
    returns = []
    for prev, cur in zip(rows, rows[1:]):
        py, pm = int(prev[0][:4]), int(prev[0][5:7])
        if _month_key(*reports.add_months(py, pm, 1)) != cur[0] or prev[1] < 100:
            continue
        added = flows.get(cur[0], 0.0) + (own.get(cur[0]) or 0.0)
        r = (cur[1] - prev[1] - added) / prev[1]
        if abs(r) <= JUMP:
            returns.append(r)
    if len(returns) < MIN_HISTORY:
        return None
    growth = math.prod(1 + r for r in returns)
    if growth <= 0:
        return None
    yearly = (growth ** (12 / len(returns)) - 1) * 100
    vol = statistics.pstdev(returns) * math.sqrt(12) * 100
    return round(yearly, 1), round(min(vol, 60.0), 1), len(returns)


def _number(value, low, high):
    try:
        v = float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return min(max(v, low), high)


def accounts(conn, setup, today):
    """Every account the projection can use, with its start, rate, volatility and where they came from."""
    ledgers = load_ledgers(conn)
    iso = today.isoformat()
    y, m = today.year, today.month
    start = reports.bounds(*reports.add_months(y, m, -12))[0]
    recent = contributions(conn, start, reports.bounds(y, m)[0])
    flows_all = contributions(conn, "0000-01-01", "9999-12-31")
    loans = {}
    for r in conn.execute("SELECT * FROM loan_terms ORDER BY start_date"):
        loans.setdefault(r["account_id"], []).append(dict(r))
    chosen = setup.get("accounts", {})
    use_global = setup.get("rate_mode") == "global"
    g_rate = _number(setup.get("global_rate"), -50, 50)
    g_vol = _number(setup.get("global_vol"), 0, 80)
    out = []
    for a in conn.execute("SELECT * FROM accounts WHERE closed IS NULL AND kind != 'held' ORDER BY sort, name"):
        led = ledgers.get(a["id"])
        balance = (led.on(iso) if led else None) or 0.0
        rate, vol, on = DEFAULTS.get(a["kind"], DEFAULTS["other"])
        source = "default for " + seed.KIND.get(a["kind"], seed.KIND["other"])["label"].lower()
        hist = None
        if a["kind"] in INVESTMENT_KINDS or a["kind"] == "savings":
            hist = history_rate(conn, a["id"], flows_all.get(a["id"], {}))
            if hist and PLAUSIBLE[0] <= hist[0] <= PLAUSIBLE[1] and hist[2] >= TRUSTED_HISTORY:
                rate, vol, source = hist[0], (hist[1] if a["kind"] != "savings" else 0.0), f"from {hist[2]} months of balances"
            elif hist and PLAUSIBLE[0] <= hist[0] <= PLAUSIBLE[1]:
                source += f" (its {hist[2]} months of history gave {hist[0]:+.1f}% a year; {TRUSTED_HISTORY} are needed to use it)"
            elif hist:
                source += f" (its history gave {hist[0]:+.0f}% a year: likely deposits, not growth)"
        pick = chosen.get(str(a["id"]), {})
        if use_global and a["kind"] in INVESTMENT_KINDS:
            if g_rate is not None:
                rate, source = g_rate, "one rate for every investment"
            if g_vol is not None:
                vol = g_vol
        else:
            if _number(pick.get("rate"), -50, 50) is not None:
                rate, source = _number(pick.get("rate"), -50, 50), "set by you"
            if _number(pick.get("vol"), 0, 80) is not None:
                vol = _number(pick.get("vol"), 0, 80)
        debt = seed.account_side(a["kind"]) == "liability"
        out.append({
            "id": a["id"], "name": a["name"], "kind": a["kind"], "debt": debt, "balance": balance,
            "rate": rate, "vol": vol, "source": source, "on": pick.get("on", on) if "on" in pick else on,
            "monthly_in": round(sum(recent.get(a["id"], {}).values()) / 12, 2),
            "loans": loans.get(a["id"], []) if debt else [],
            "set_rate": pick.get("rate", ""), "set_vol": pick.get("vol", ""),
        })
    return out


CONFIDENCE = {"expected": "on the expected path", "0.75": "in 3 of 4 futures", "0.9": "in 9 of 10 futures"}


def _goal(setup, today, ids, kinds):
    """The "what would it take" question, cleaned up, or None: reach `amount` by month `by` (today's or
    future dollars) across the accounts of interest (`measure`, debts counting against it), saving each
    month split across accounts (`into`: {account id: share}). Accounts saved into count toward the goal."""
    g = setup.get("goal") or {}
    amount = _number(g.get("amount"), 1, 1e12)
    by = str(g.get("by") or "")
    if amount is None or len(by) != 7 or by <= today.isoformat()[:7]:
        return None
    into = g.get("into")
    if not isinstance(into, dict):  # an older single account
        into = {str(into): 100} if into else {}
    shares = {int(k): _number(v, 0, 100) or 0.0 for k, v in into.items() if str(k).isdigit()}
    shares = {k: v for k, v in shares.items() if v > 0 and k in ids and kinds[k] not in ("credit", "loan")}
    total = sum(shares.values())
    if not total:
        return None
    measure = g.get("measure")
    if not isinstance(measure, list):  # older: "worth", "investments" or one account
        measure = (list(ids) if measure == "worth" else [i for i in ids if kinds[i] in INVESTMENT_KINDS] if measure == "investments"
                   else [measure] if measure else [])
    measure = {int(i) for i in measure if str(i).isdigit() and int(i) in ids} | set(shares)
    months = (int(by[:4]) - today.year) * 12 + int(by[5:]) - today.month
    return {"amount": amount, "by": by, "months": min(months, 40 * 12), "real": bool(g.get("real")),
            "into": {k: v / total for k, v in shares.items()}, "measure": sorted(measure),
            "confidence": g.get("confidence") if g.get("confidence") in CONFIDENCE else "expected"}


def _quantile(values, q):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(q * len(ordered))))]


def project(conn, setup=None, today=None, runs=RUNS):
    today = today or date.today()
    setup = load_setup(conn) if setup is None else setup
    years = int(_number(setup.get("years"), 1, 40) or 20)
    real = bool(setup.get("real"))
    inflation = _number(setup.get("inflation"), 0, 15)
    inflation = 2.5 if inflation is None else inflation
    accts = accounts(conn, setup, today)
    values = budgeting.plan_values(conn)
    plan_ready = values["plan_income"] is not None
    with_plan = plan_ready and setup.get("plan", True)
    plan_acct = budgeting.get_setting(conn, "plan_account", cast=int)
    backups = [(budgeting.get_setting(conn, k, cast=int), budgeting.get_setting(conn, mk) or 0.0)
               for k, mk in zip(budgeting.BACKUP_KEYS, budgeting.BACKUP_MIN_KEYS)]
    goal = _goal(setup, today, {a["id"] for a in accts}, {a["id"]: a["kind"] for a in accts})
    needed_ids = {plan_acct, values["savings_account"], *(b for b, _ in backups)} if with_plan else set()
    if goal:
        needed_ids |= set(goal["into"]) | set(goal["measure"])
    for a in accts:  # the plan's accounts and the goal's come along: money moves through them
        if a["id"] in needed_ids and not a["on"]:
            a["forced"] = True
            a["on"] = True
    used = [a for a in accts if a["on"]]
    ids = [a["id"] for a in used]
    backups = [(b, f) for b, f in backups if b in ids]
    buffer = values["plan_buffer"] or 0.0
    items = budgeting.plan_items(conn) if with_plan else []
    # The Plan's months, changes included, run through its last month; after that the planned month
    # carries on as it is now (the baseline), without the changes.
    plan_end, plan_months, _ = budgeting.plan_end(conn, today)
    horizon = years * 12
    months = max(horizon, goal["months"] if goal else 0)

    # Month by month, what doesn't depend on markets: plan nets, loan balances.
    keys, nets = [], []
    y, m = today.year, today.month
    for _ in range(months):
        y, m = reports.add_months(y, m, 1)
        key = _month_key(y, m)
        keys.append(key)
        nets.append(budgeting.plan_month(values, items if key <= plan_end else [], key)[3] if with_plan else 0.0)
    saving = values["savings"] if with_plan and values["mode"] == "savings" else 0.0
    save_to = values["savings_account"] if values["savings_account"] in ids else None
    loan_path = {}
    for a in used:
        if a["loans"]:
            loan_path[a["id"]] = [sum(loan_balance(l["principal"], l["annual_rate"], l["term_months"], l["start_date"], f"{k}-28")
                                      for l in a["loans"]) for k in keys]

    # Investments: the accounts that swing. Their mix sets the drawdown and N0 figures.
    invest = [a for a in used if a["kind"] in INVESTMENT_KINDS and a["vol"] > 0 and a["balance"] > 0]
    invest_total = sum(a["balance"] for a in invest)
    weights = {a["id"]: a["balance"] / invest_total for a in invest} if invest_total else {}
    debts = {a["id"] for a in used if a["debt"]}

    def worth(bal):
        return sum(-bal[a["id"]] if a["debt"] else bal[a["id"]] for a in used)

    def measured(bal):
        return sum(-bal[i] if i in debts else bal[i] for i in goal["measure"])

    def run(rng):
        """One future: balances at each year's end, the first month the plan runs short, the biggest fall of
        the investments from a high, and for the goal the measure then and what $1 a month saved grows to."""
        bal = {a["id"]: a["balance"] for a in used}
        yearly, short_at = [], None
        params = []
        for a in used:
            sm = a["vol"] / 100 / math.sqrt(12)
            mu = math.log(1 + a["rate"] / 100) / 12
            params.append((a["id"], a["debt"], mu - sm * sm / 2 if rng else mu, sm if rng else 0.0, a["monthly_in"]))
        index = peak = 1.0
        drop = 0.0
        per_dollar = dict.fromkeys(goal["into"], 0.0) if goal else {}
        at_goal = None
        for t in range(months):
            z = rng.gauss(0, 1) if rng else 0.0
            moved = 0.0
            for aid, debt, mu, sm, added in params:
                if aid in loan_path:
                    bal[aid] = loan_path[aid][t]
                    continue
                g = math.exp(mu + sm * z)
                if aid in weights:
                    moved += weights[aid] * g
                if aid in per_dollar:  # $1 saved here at each month's end, grown since
                    per_dollar[aid] = per_dollar[aid] * g + 1
                if bal[aid] > 0 or debt:
                    bal[aid] *= g
                bal[aid] += added
            if weights:
                index *= moved
                peak = max(peak, index)
                drop = max(drop, 1 - index / peak)
            if with_plan and plan_acct in bal:
                bal[plan_acct] += nets[t]
                if save_to is not None:
                    bal[save_to] += saving
                for b, floor in backups:
                    if bal[plan_acct] >= buffer:
                        break
                    take = min(buffer - bal[plan_acct], max(bal[b] - floor, 0.0))
                    bal[b] -= take
                    bal[plan_acct] += take
                if short_at is None and bal[plan_acct] < buffer - 0.005:
                    short_at = keys[t]
            if goal and t == goal["months"] - 1:
                at_goal = (measured(bal), sum(share * per_dollar[aid] for aid, share in goal["into"].items()))
            if t < horizon and ((t + 1) % 12 == 0 or t == horizon - 1):
                yearly.append((t + 1, dict(bal)))
        return {"yearly": yearly, "short": short_at, "drop": drop, "goal": at_goal}

    expected = run(None)
    rng = random.Random(SEED)
    sims = [run(rng) for _ in range(runs)]

    start_worth = sum(-a["balance"] if a["debt"] else a["balance"] for a in used)
    rows = []
    for i, (elapsed, bal) in enumerate(expected["yearly"]):
        f = (1 + inflation / 100) ** -(elapsed / 12) if real else 1.0
        spread = [worth(s["yearly"][i][1]) * f for s in sims]
        y, m = reports.add_months(today.year, today.month, elapsed)
        rows.append({
            "year": y, "key": _month_key(y, m), "partial": elapsed % 12 != 0, "worth": worth(bal) * f, "low": _quantile(spread, 0.10),
            "mid": _quantile(spread, 0.50), "high": _quantile(spread, 0.90),
            "behind": sum(1 for v in spread if v < start_worth) / len(spread) if spread else 0.0,
            "accounts": {aid: bal[aid] * f for aid in ids},
        })

    # The investments' mix as one: its rate, swings, and N0 (years until the expected growth is as big as
    # one standard deviation of the swings: (sigma / mu)^2 with mu the growth after volatility drag).
    mix = None
    if weights:
        rate = sum(weights[a["id"]] * a["rate"] for a in invest) / 100
        vol = sum(weights[a["id"]] * a["vol"] for a in invest) / 100
        drift = math.log(1 + rate) - vol * vol / 2
        drops = [s["drop"] for s in sims]
        mix = {"rate": rate * 100, "vol": vol * 100, "total": invest_total,
               "n0": (vol / drift) ** 2 if drift > 0 and vol > 0 else None,
               "drop_typical": _quantile(drops, 0.5), "drop_bad": _quantile(drops, 0.9),
               "behind_by_year": {n: _normal_cdf(-drift * math.sqrt(n) / vol) if vol else 0.0 for n in (1, 5, 10, 20)}}

    return {
        "years": years, "real": real, "inflation": inflation, "accounts": accts, "used": used, "rows": rows,
        "start_worth": start_worth, "with_plan": with_plan, "plan_ready": plan_ready, "plan_account": plan_acct,
        "first_short": expected["short"], "short_share": sum(1 for s in sims if s["short"]) / len(sims) if sims else 0.0,
        "rate_mode": "global" if setup.get("rate_mode") == "global" else "account",
        "global_rate": setup.get("global_rate", ""), "global_vol": setup.get("global_vol", ""),
        "savings": saving, "save_to": save_to, "runs": runs, "mix": mix,
        "goal": _solve(goal, expected, sims, inflation) if goal else None, "goal_setup": setup.get("goal") or {},
        "plan_income": values["plan_income"], "plan_end": plan_end, "plan_months": plan_months,
        "span": horizon / 12,
    }


def _normal_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _solve(goal, expected, sims, inflation):
    """How much a month, saved into the goal's account on top of everything else, reaches the goal: on the
    expected path, or in 3 of 4 / 9 of 10 futures. Each future's measure at the goal date is its value
    without the extra plus the extra times what $1 a month grows to in it, so the answer is exact for the
    simulated futures (backup draws aside)."""
    years = goal["months"] / 12
    target = goal["amount"] * ((1 + inflation / 100) ** years if goal["real"] else 1.0)
    base, per = expected["goal"]
    futures = [s["goal"] for s in sims]
    if goal["confidence"] == "expected":
        needed = (target - base) / per if per else None
    else:
        share = 1 - float(goal["confidence"])  # e.g. 9 in 10 reach it: the 10th percentile must
        lo, hi = 0.0, max(target, 1.0)
        if _quantile([v + hi * f for v, f in futures], share) < target:
            hi = None
        elif _quantile([v for v, _ in futures], share) >= target:
            hi = 0.0
        else:
            for _ in range(60):
                mid = (lo + hi) / 2
                if _quantile([v + mid * f for v, f in futures], share) >= target:
                    hi = mid
                else:
                    lo = mid
        needed = hi
    shown = (lambda v: v / (1 + inflation / 100) ** years) if goal["real"] else (lambda v: v)
    reach_now = sum(1 for v, _ in futures if v >= target) / len(futures) if futures else 0.0
    return {**goal, "target": target, "needed": None if needed is None else max(needed, 0.0),
            "on_track": needed is not None and needed <= 0, "label": CONFIDENCE[goal["confidence"]],
            "expected_without": shown(base), "reach_without": reach_now}
