"""Business page: income, costs and profit for each business account.

A business account is an account with its own default categories (Accounts -> Business accounts).
Its income and costs are every transaction in those two categories, on any account, so a business cost
picked by hand on a household card counts, and a personal charge made on the business card doesn't.
Transfers into and out of the business account are shown apart: money you put in or took out.
Months count on the effective date, like every other report.
"""
from datetime import date

from .balances import month_range


def businesses(conn):
    """[account row] for accounts with default categories, open ones first."""
    return [dict(r) for r in conn.execute(
        """SELECT a.*, ci.name AS income_name, co.name AS cost_name FROM accounts a
           LEFT JOIN categories ci ON ci.id = a.default_in_category
           LEFT JOIN categories co ON co.id = a.default_out_category
           WHERE a.default_in_category IS NOT NULL OR a.default_out_category IS NOT NULL
           ORDER BY a.closed IS NOT NULL, a.sort, a.name"""
    )]


def _money(v):
    return round(v or 0, 2)


def report(conn, b, today=None):
    """Months (newest first), years, totals and recent transactions for one business account."""
    today = today or date.today().isoformat()
    inc, cost = b["default_in_category"], b["default_out_category"]
    by_month = {}
    for m, income, spent in conn.execute(
        """SELECT substr(effective_date, 1, 7),
                  SUM(CASE WHEN category_id = ? THEN amount END), SUM(CASE WHEN category_id = ? THEN amount END)
           FROM transactions WHERE category_id IN (?, ?) GROUP BY 1""",
        (inc, cost, inc, cost),
    ):
        by_month.setdefault(m, {})["income"] = _money(income)
        by_month[m]["costs"] = _money(-(spent or 0))  # costs are money out: shown as a positive number
    for m, put_in, took_out in conn.execute(
        """SELECT substr(t.effective_date, 1, 7), SUM(CASE WHEN t.amount > 0 THEN t.amount END),
                  SUM(CASE WHEN t.amount < 0 THEN -t.amount END)
           FROM transactions t JOIN categories c ON c.id = t.category_id
           WHERE t.account_id = ? AND c.kind = 'transfer' GROUP BY 1""",
        (b["id"],),
    ):
        by_month.setdefault(m, {}).update(put_in=_money(put_in), took_out=_money(took_out))
    first = min([*by_month, b["opened"] or today[:7]])
    months = []
    for m in month_range(first, today[:7]):
        v = by_month.get(m, {})
        row = {"month": m, "income": v.get("income", 0.0), "costs": v.get("costs", 0.0),
               "put_in": v.get("put_in", 0.0), "took_out": v.get("took_out", 0.0)}
        row["profit"] = _money(row["income"] - row["costs"])
        months.append(row)

    def total(rows):
        t = {k: _money(sum(r[k] for r in rows)) for k in ("income", "costs", "profit", "put_in", "took_out")}
        t["months"] = len(rows)
        return t

    years = [(y, total([r for r in months if r["month"][:4] == y])) for y in sorted({r["month"][:4] for r in months}, reverse=True)]
    recent = [dict(r) for r in conn.execute(
        """SELECT t.id, t.effective_date, t.amount, t.name, a.name AS account, c.name AS category
           FROM transactions t JOIN accounts a ON a.id = t.account_id JOIN categories c ON c.id = t.category_id
           WHERE t.category_id IN (?, ?) ORDER BY t.effective_date DESC, t.id DESC LIMIT 15""",
        (inc, cost),
    )]
    last12 = months[-12:]
    return {
        "account": b, "months": list(reversed(months)), "years": years, "all": total(months),
        "this_year": dict(years).get(today[:4], total([])), "last12": total(last12),
        "chart": [{"month": r["month"], "income": r["income"], "costs": r["costs"]} for r in last12],
        "recent": recent,
    }
