"""Recurring page: regular charges and subscriptions, found in the transactions already there.

Spending (expense categories, or uncategorized money out; never transfers) is grouped by merchant: the
clean name, upper-cased, on any account. Charges on the same day are added together. A merchant is
recurring when the gaps between its charges fit one cadence (most of them inside its range, a skipped
cycle allowed), with enough charges. A merchant that doesn't fit as a whole (a store that also bills a
membership) is tried once more per exact amount, so "Amazon 14.99 every month" can still show up.

Amounts: a bill whose charges sit on one or two price levels is fixed and shows its latest price;
anything else (utilities) is "varies" and shows the median. Flags, against today:
  new        first charged within the last couple of cycles (and the data goes back further than that);
             for a single amount of a bigger merchant, that amount first charged then
  price_up   the latest price is over 5% and $1 above the earlier price (charged at least twice), changed
             within the last couple of cycles; not for bills that vary
  missed     overdue by more than half a cycle: maybe cancelled
  stopped    nothing for two cycles or more: listed under Ended, left out of the totals
Merchants marked "Not recurring" are kept in recurring_ignored and listed apart.
"""
from datetime import date, timedelta
from statistics import median

from .reports import IS_SPEND
from .rules import normalize

# key, label, days per cycle, gap range, fewest charges, months per cycle (None = count days), per month
CADENCES = [
    ("weekly", "Weekly", 7, (5, 9), 4, None),
    ("biweekly", "Every 2 weeks", 14, (11, 17), 4, None),
    ("monthly", "Monthly", 30.44, (25, 35), 3, 1),
    ("quarterly", "Quarterly", 91.3, (80, 100), 3, 3),
    ("yearly", "Yearly", 365.25, (345, 385), 2, 12),
]
CADENCE_LABELS = {c[0]: c[1] for c in CADENCES}
WINDOW_DAYS = 548        # ~18 months of history for weekly to quarterly
YEARLY_WINDOW_DAYS = 760  # two renewals and a bit, for yearly
REGULAR_SHARE = 0.8      # this share of the gaps must fit the cadence (one cycle, or a skipped one)...
RECENT_GAPS = 12         # ...of the latest this many
SAME_PRICE = 0.02        # charges within 2% count as the same price


def _add_months(d, n):
    idx = d.year * 12 + d.month - 1 + n
    y, m = divmod(idx, 12)
    m += 1
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, last))


def _next_date(last, cadence):
    _, _, days, _, _, months = cadence
    return _add_months(last, months) if months else last + timedelta(days=days)


def _recent_days(cadence):
    """How far back counts as "lately" for New and Price went up: about two cycles."""
    key, _, days, _, _, _ = cadence
    return 400 if key == "yearly" else 2.5 * days


def _fit(dates, cadence, fewest=0):
    """True when the gaps between these (sorted, distinct) dates fit the cadence."""
    _, _, _, (lo, hi), least, _ = cadence
    fewest = max(fewest, least)
    if lo >= 25:  # monthly and longer: charges a few days apart are one bill (a split or corrected charge)
        kept = dates[:1]
        for d in dates[1:]:
            if (d - kept[-1]).days > 3:
                kept.append(d)
        dates = kept
    if len(dates) < fewest:
        return False
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    regular = sum(lo <= g <= hi for g in gaps)
    lately = gaps[-RECENT_GAPS:]  # how it bills now matters more than a messy start
    fits = sum(lo <= g <= hi or 2 * lo <= g <= 2 * hi for g in lately)
    return regular >= fewest - 1 and fits >= REGULAR_SHARE * len(lately) and lo <= median(lately) <= hi


def _detect(charges, today, fewest=0):
    """The cadence these [(date, amount)] charges fit, shortest first; (None, None) if none fits."""
    for cadence in CADENCES:
        window = YEARLY_WINDOW_DAYS if cadence[0] == "yearly" else WINDOW_DAYS
        since = today - timedelta(days=window)
        inside = [c for c in charges if c[0] >= since]
        if len(inside) == 2 and not _same_ish(inside[0][1], inside[1][1]):
            continue  # just two charges a year apart: only when they're about the same
        if _fit([d for d, _ in inside], cadence, fewest):
            return cadence, inside
    return None, None


def _same_ish(a, b):
    return abs(a - b) <= 0.2 * max(abs(a), abs(b))


def _same(a, b):
    return abs(a - b) <= SAME_PRICE * max(abs(a), abs(b), 0.01)


def _run(amounts):
    """How many charges in a row at the last one's price."""
    run = 1
    while run < len(amounts) and _same(amounts[-run - 1], amounts[-1]):
        run += 1
    return run


def _levels(amounts):
    """(latest price, charges in a row at it, the price before that, charges in a row at that one)."""
    run = _run(amounts)
    before = amounts[:-run]
    if not before:
        return amounts[-1], run, None, 0
    return amounts[-1], run, median(before[-_run(before):]), _run(before)


def _describe(key, name, category, charges, cadence, first_seen, history_start, today):
    amounts = [a for _, a in charges]
    last_amount = amounts[-1]
    latest, run, previous, prev_run = _levels(amounts)
    if run == 1 and prev_run >= 2 and not previous / 1.5 <= latest <= previous * 1.5:
        # One charge far off the usual (an extra purchase the same day, a partial month): not a new price.
        amounts = amounts[:-1]
        latest, run, previous, prev_run = _levels(amounts)
    lately = amounts[-6:]  # fixed or not, judged on the last few charges
    fixed = sum(_same(a, latest) or (previous is not None and _same(a, previous)) for a in lately) >= 0.75 * len(lately)
    typical = latest if fixed else median(amounts[-12:])
    days = cadence[2]
    per_month = typical * 30.44 / days
    last = charges[-1][0]
    since_last = (today - last).days
    stopped = since_last > 2 * days + (cadence[3][1] - days)
    recent = today - timedelta(days=_recent_days(cadence))
    price_up = (fixed and prev_run >= 2 and latest > previous * 1.05 and latest - previous > 1
                and charges[len(amounts) - run][0] >= recent and not stopped)
    return {
        "key": key, "name": name, "category": category, "cadence": cadence[0], "cadence_label": cadence[1],
        "typical": round(typical, 2), "varies": not fixed, "monthly": round(per_month, 2),
        "yearly": round(per_month * 12, 2), "count": len(charges), "first": first_seen.isoformat(),
        "last": last.isoformat(), "last_amount": round(last_amount, 2), "next": _next_date(last, cadence).isoformat(),
        "previous": round(previous, 2) if price_up else None,
        "new": first_seen >= recent and history_start <= first_seen - timedelta(days=days) and not stopped,
        "price_up": price_up,
        "missed": not stopped and since_last > 1.5 * days,
        "stopped": stopped,
    }


def ignored_keys(conn):
    return {r[0] for r in conn.execute("SELECT key FROM recurring_ignored")}


def find(conn, today=None):
    """Every recurring merchant, ignored ones included (marked), newest data as of `today`."""
    today = today or date.today()
    since = (today - timedelta(days=YEARLY_WINDOW_DAYS)).isoformat()
    by_merchant = {}
    for r in conn.execute(
        # A charge split across categories counts once, whole: its bank row at the bank's amount.
        f"""SELECT t.date, -COALESCE(t.split_total, t.amount) AS amount, t.name, t.raw_description, c.name AS category
            FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
            WHERE COALESCE(t.split_total, t.amount) < 0 AND t.split_of IS NULL AND {IS_SPEND} AND t.date >= ? AND t.date <= ?
            ORDER BY t.date, t.id""",
        (since, today.isoformat()),
    ):
        key = normalize(r["name"]) or normalize(r["raw_description"])
        if key:
            by_merchant.setdefault(key, []).append(r)
    if not by_merchant:
        return []
    history_start = date.fromisoformat(conn.execute("SELECT MIN(date) FROM transactions").fetchone()[0][:10])
    firsts = {}
    for name, first in conn.execute(
        f"""SELECT t.name, MIN(t.date) FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.amount < 0 AND {IS_SPEND} GROUP BY t.name"""
    ):
        k = normalize(name)
        firsts[k] = min(firsts.get(k, first), first)
    ignored = ignored_keys(conn)

    out = []
    for key, rows in by_merchant.items():
        name, category = rows[-1]["name"], rows[-1]["category"]
        first_seen = date.fromisoformat(firsts.get(key, rows[0]["date"])[:10])

        def daily(rs):
            totals = {}
            for r in rs:
                d = date.fromisoformat(r["date"][:10])
                totals[d] = totals.get(d, 0.0) + r["amount"]
            return sorted(totals.items())

        charges = daily(rows)
        cadence, inside = _detect(charges, today)
        if cadence:
            out.append(_describe(key, name, category, inside, cadence, first_seen, history_start, today))
            continue
        # A merchant with other charges too: try each exact amount on its own.
        by_amount = {}
        for r in rows:
            by_amount.setdefault(round(r["amount"], 2), []).append(r)
        for amount, group in by_amount.items():
            if len(group) < 2:
                continue
            cadence, inside = _detect(daily(group), today, fewest=3)  # an amount repeating by chance is likelier
            if cadence:
                item = _describe(f"{key}|{amount:.2f}", name, category, inside, cadence,
                                 date.fromisoformat(group[0]["date"][:10]), history_start, today)
                out.append(item)
    for item in out:
        item["ignored"] = item["key"] in ignored
    out.sort(key=lambda i: (-i["monthly"], i["name"]))
    return out


def report(conn, today=None):
    """What the Recurring page shows: active items, ended ones, ignored ones, and totals."""
    items = find(conn, today)
    active = [i for i in items if not i["ignored"] and not i["stopped"]]
    monthly = round(sum(i["monthly"] for i in active), 2)
    return {
        "active": active,
        "ended": sorted((i for i in items if not i["ignored"] and i["stopped"]), key=lambda i: i["last"], reverse=True),
        "ignored": sorted((i for i in items if i["ignored"]), key=lambda i: i["name"]),
        "monthly": monthly, "yearly": round(monthly * 12, 2),
        "new": sum(i["new"] for i in active), "price_up": sum(i["price_up"] for i in active),
        "missed": sum(i["missed"] for i in active),
        "cadences": [(k, label, sum(i["cadence"] == k for i in active)) for k, label, *_ in CADENCES
                     if any(i["cadence"] == k for i in active)],
    }


def summary(conn, today=None):
    """Counts for a notification: {"count", "monthly", "new", "price_up", "missed", "new_items", "price_up_items"}.

    Reads only; one query over the last two years of spending.
    """
    r = report(conn, today)
    pick = lambda flag: [{"name": i["name"], "typical": i["typical"], "previous": i["previous"], "cadence": i["cadence"]}  # noqa: E731
                         for i in r["active"] if i[flag]]
    return {"count": len(r["active"]), "monthly": r["monthly"], "new": r["new"], "price_up": r["price_up"],
            "missed": r["missed"], "new_items": pick("new"), "price_up_items": pick("price_up")}


def ignore(conn, key, name=None):
    conn.execute("INSERT OR REPLACE INTO recurring_ignored (key, name) VALUES (?, ?)", (key, name))


def restore(conn, key):
    conn.execute("DELETE FROM recurring_ignored WHERE key = ?", (key,))
