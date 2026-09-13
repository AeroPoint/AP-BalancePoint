"""Aggregations behind the dashboard and trends pages.

Money-flow conventions:
  * income   = income categories only; uncategorized deposits are often transfers, so they're
               reported separately (see uncategorized_deposits) until you categorize them
  * spending = expense categories (refunds net against them), plus uncategorized money going out
  * transfer categories (card payments, moving money, investing) are left out of both

Every report takes an optional list of account ids; None or an empty list means all accounts.
"""
from datetime import date

MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
JOIN = "FROM transactions t LEFT JOIN categories c ON c.id = t.category_id"
IS_SPEND = "(c.kind = 'expense' OR (c.id IS NULL AND t.amount < 0))"
IS_INCOME = "(c.kind = 'income')"


def _scope(accounts):
    if not accounts:
        return "", ()
    return f" AND t.account_id IN ({', '.join('?' * len(accounts))})", tuple(accounts)


def add_months(year, month, n):
    idx = year * 12 + (month - 1) + n
    return idx // 12, idx % 12 + 1


def bounds(year, month=None):
    """[start, end) ISO dates for a year or a single month."""
    if month is None:
        return f"{year}-01-01", f"{year + 1}-01-01"
    ny, nm = add_months(year, month, 1)
    return f"{year}-{month:02d}-01", f"{ny}-{nm:02d}-01"


def years(conn):
    return [int(r[0]) for r in conn.execute("SELECT DISTINCT substr(date, 1, 4) FROM transactions ORDER BY 1 DESC")]


def latest_month(conn, accounts=None):
    scope, extra = _scope(accounts)
    latest = conn.execute(f"SELECT MAX(t.date) FROM transactions t WHERE 1 = 1{scope}", extra).fetchone()[0]
    if not latest:
        today = date.today()
        return today.year, today.month
    return int(latest[:4]), int(latest[5:7])


def totals(conn, start, end, accounts=None):
    scope, extra = _scope(accounts)
    income, spending, count = conn.execute(
        f"""SELECT COALESCE(SUM(CASE WHEN {IS_INCOME} THEN t.amount END), 0),
                   COALESCE(SUM(CASE WHEN {IS_SPEND} THEN -t.amount END), 0),
                   COUNT(*)
            {JOIN} WHERE t.date >= ? AND t.date < ?{scope}""",
        (start, end, *extra),
    ).fetchone()
    net = income - spending
    return {
        "income": income,
        "spending": spending,
        "net": net,
        "count": count,
        "rate": net / income if income > 0 else None,
    }


def uncategorized_deposits(conn, start, end, accounts=None):
    scope, extra = _scope(accounts)
    n, total = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(t.amount), 0) FROM transactions t "
        f"WHERE t.category_id IS NULL AND t.amount > 0 AND t.date >= ? AND t.date < ?{scope}",
        (start, end, *extra),
    ).fetchone()
    return {"n": n, "total": total}


def by_category(conn, start, end, kind="expense", accounts=None):
    scope, extra = _scope(accounts)
    cond, sign = (IS_SPEND, -1) if kind == "expense" else (IS_INCOME, 1)
    rows = conn.execute(
        f"""SELECT c.id, COALESCE(c.name, 'Uncategorized') AS name, SUM(t.amount) * ? AS total, COUNT(*) AS n
            {JOIN} WHERE t.date >= ? AND t.date < ? AND {cond}{scope}
            GROUP BY c.id ORDER BY total DESC""",
        (sign, start, end, *extra),
    )
    return [dict(r) for r in rows if round(r["total"], 2) > 0]


def by_merchant(conn, start, end, limit=10, accounts=None):
    scope, extra = _scope(accounts)
    rows = conn.execute(
        f"""SELECT t.name, -SUM(t.amount) AS total, COUNT(*) AS n
            {JOIN} WHERE t.date >= ? AND t.date < ? AND {IS_SPEND}{scope}
            GROUP BY t.name HAVING total > 0 ORDER BY total DESC LIMIT ?""",
        (start, end, *extra, limit),
    )
    return [dict(r) for r in rows]


def monthly(conn, start_year, start_month, count, accounts=None):
    """Income/spending/net for `count` consecutive months."""
    scope, extra = _scope(accounts)
    months = [add_months(start_year, start_month, i) for i in range(count)]
    start, end = bounds(*months[0])[0], bounds(*months[-1])[1]
    rows = conn.execute(
        f"""SELECT substr(t.date, 1, 7) AS m,
                   SUM(CASE WHEN {IS_INCOME} THEN t.amount ELSE 0 END) AS income,
                   SUM(CASE WHEN {IS_SPEND} THEN -t.amount ELSE 0 END) AS spending
            {JOIN} WHERE t.date >= ? AND t.date < ?{scope} GROUP BY m""",
        (start, end, *extra),
    )
    found = {r["m"]: r for r in rows}
    out = []
    for y, m in months:
        key = f"{y}-{m:02d}"
        r = found.get(key)
        income, spending = (r["income"], r["spending"]) if r else (0.0, 0.0)
        out.append({
            "key": key,
            "label": MONTH_NAMES[m - 1],
            "year": y,
            "month": m,
            "income": round(income, 2),
            "spending": round(spending, 2),
            "net": round(income - spending, 2),
            "has_data": r is not None,
        })
    return out


def category_averages(conn, year, month, months=12, accounts=None):
    """Average monthly spending per category over the `months` before (year, month)."""
    scope, extra = _scope(accounts)
    sy, sm = add_months(year, month, -months)
    start, end = bounds(sy, sm)[0], bounds(year, month)[0]
    n = conn.execute(
        f"SELECT COUNT(DISTINCT substr(t.date, 1, 7)) FROM transactions t WHERE t.date >= ? AND t.date < ?{scope}",
        (start, end, *extra),
    ).fetchone()[0]
    if not n:
        return {}, 0
    return {r["id"]: r["total"] / n for r in by_category(conn, start, end, accounts=accounts)}, n


def category_matrix(conn, year, accounts=None):
    """Spending per category per month for one year, with average and same-period-last-year."""
    scope, extra = _scope(accounts)
    start, end = bounds(year)
    grid = {}
    rows = conn.execute(
        f"""SELECT c.id, COALESCE(c.name, 'Uncategorized') AS name,
                   CAST(substr(t.date, 6, 2) AS INTEGER) AS m, -SUM(t.amount) AS total
            {JOIN} WHERE t.date >= ? AND t.date < ? AND {IS_SPEND}{scope}
            GROUP BY c.id, m""",
        (start, end, *extra),
    )
    for r in rows:
        entry = grid.setdefault(r["id"], {"id": r["id"], "name": r["name"], "months": [0.0] * 12})
        entry["months"][r["m"] - 1] = r["total"]

    active = [
        int(r[0])
        for r in conn.execute(
            f"SELECT DISTINCT CAST(substr(t.date, 6, 2) AS INTEGER) FROM transactions t "
            f"WHERE t.date >= ? AND t.date < ?{scope} ORDER BY 1",
            (start, end, *extra),
        )
    ]
    last_month = active[-1] if active else 12
    prev = {
        r["id"]: r["total"]
        for r in by_category(conn, f"{year - 1}-01-01", bounds(year - 1, last_month)[1], accounts=accounts)
    }

    out = []
    for e in grid.values():
        e["total"] = sum(e["months"])
        e["average"] = e["total"] / (len(active) or 1)
        e["prev_total"] = prev.get(e["id"])
        if round(e["total"], 2) > 0:
            out.append(e)
    out.sort(key=lambda e: -e["total"])
    month_totals = [sum(e["months"][i] for e in out) for i in range(12)]
    return out, month_totals, active


def cumulative_spending(conn, year_list, accounts=None):
    """Running spending total by month for each year (None after a year's last month of data)."""
    series = []
    for y in year_list:
        months = monthly(conn, y, 1, 12, accounts)
        last = max((i for i, m in enumerate(months) if m["has_data"]), default=-1)
        running, values = 0.0, []
        for i, m in enumerate(months):
            running += m["spending"]
            values.append(round(running, 2) if i <= last else None)
        series.append({"name": str(y), "values": values})
    return series


def stacked_by_category(conn, year, top_n=7, accounts=None):
    """Monthly spending split into your biggest all-time categories + Other.

    The category set comes from every account and every year, so a category keeps its color
    when you switch years or accounts.
    """
    scope, extra = _scope(accounts)
    top = by_category(conn, "0000-01-01", "9999-12-31")[:top_n]
    top_ids = [c["id"] for c in top]
    start, end = bounds(year)
    series = {cid: [0.0] * 12 for cid in top_ids}
    other = [0.0] * 12
    rows = conn.execute(
        f"""SELECT c.id, CAST(substr(t.date, 6, 2) AS INTEGER) AS m, -SUM(t.amount) AS total
            {JOIN} WHERE t.date >= ? AND t.date < ? AND {IS_SPEND}{scope} GROUP BY c.id, m""",
        (start, end, *extra),
    )
    for r in rows:
        target = series[r["id"]] if r["id"] in series else other
        target[r["m"] - 1] += r["total"]
    result = [{"name": c["name"], "values": [round(v, 2) for v in series[c["id"]]]} for c in top]
    result.append({"name": "Other", "values": [round(v, 2) for v in other]})
    return result
