"""Account balances on any day.

An account's balance on a day is the latest balance entered on or before that day, plus, for
accounts with transactions, everything that posted after it. Entering the bank's real number now
and then snaps the estimate back. Accounts without transactions (retirement, a home) simply carry
their last balance forward. Loans with fixed terms are calculated instead, the way a spreadsheet's
=-FV(rate/12, DATEDIF(start, date, "m"), PMT(rate/12, term, amount), amount) formula would, until the
lender's own number comes in from the bank sync: a synced balance wins from its date on, for as long as
it's fresh (SYNCED_FRESH_DAYS), so the schedule takes back over if the sync ever stops.
"""
import bisect
from collections import defaultdict
from datetime import date, timedelta

from . import seed

SYNCED_FRESH_DAYS = 35


def month_end(month):
    """'2026-02' -> '2026-02-28'."""
    y, m = int(month[:4]), int(month[5:7])
    following = date(y + (m == 12), m % 12 + 1, 1)
    return (following - timedelta(days=1)).isoformat()


def month_range(first, last):
    out, (y, m) = [], (int(first[:4]), int(first[5:7]))
    while (y, m) <= (int(last[:4]), int(last[5:7])):
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def months_elapsed(start, day):
    """Whole months from start to day, like Excel's DATEDIF(start, day, "m")."""
    s, d = date.fromisoformat(start), date.fromisoformat(day)
    return (d.year - s.year) * 12 + (d.month - s.month) - (d.day < s.day)


def loan_payment(principal, annual_rate, term_months):
    """Monthly principal and interest."""
    r = annual_rate / 100 / 12
    if r == 0:
        return principal / term_months
    return principal * r / (1 - (1 + r) ** -term_months)


def loan_balance(principal, annual_rate, term_months, start_date, day):
    """What's still owed on an amortizing loan after the monthly payments made since start_date."""
    n = max(0, min(months_elapsed(start_date, day), term_months))
    r = annual_rate / 100 / 12
    if r == 0:
        return principal * (1 - n / term_months)
    growth = (1 + r) ** n
    return principal * growth - loan_payment(principal, annual_rate, term_months) * (growth - 1) / r


class AccountLedger:
    def __init__(self, account, anchors, daily, loans=(), synced=()):
        self.account = account
        self.loans = list(loans)
        self.synced = sorted(synced)  # [(as_of, amount)] balances that came from the bank sync
        self.synced_days = [s[0] for s in self.synced]
        self.side = seed.account_side(account["kind"])
        self.anchors = sorted(anchors)  # [(as_of, amount)]
        self.anchor_days = [a[0] for a in self.anchors]
        self.days, self.cum_amount, self.cum_count = [], [], []
        amount = count = 0
        for day, total, n in daily:
            amount += total
            count += n
            self.days.append(day)
            self.cum_amount.append(amount)
            self.cum_count.append(count)
        self.has_transactions = bool(self.days)

    def _through(self, series, day):
        i = bisect.bisect_right(self.days, day)
        return series[i - 1] if i else 0

    def since_anchor(self, day):
        """(latest (as_of, amount) on or before day, transactions posted after it through day)."""
        i = bisect.bisect_right(self.anchor_days, day)
        if not i:
            return None, 0
        anchor = self.anchors[i - 1]
        return anchor, self._through(self.cum_count, day) - self._through(self.cum_count, anchor[0])

    def scheduled_loans(self, day):
        """Loans whose calculated balance applies on this day."""
        return [loan for loan in self.loans if (loan["counts_from"] or loan["start_date"]) <= day]

    def synced_on(self, day):
        """The bank-synced balance that applies on a day, if a fresh one exists."""
        i = bisect.bisect_right(self.synced_days, day)
        if i and (date.fromisoformat(day) - date.fromisoformat(self.synced[i - 1][0])).days <= SYNCED_FRESH_DAYS:
            return self.synced[i - 1]
        return None

    def calculated_loans(self, day):
        """Loans whose schedule sets the balance on a day: none while the lender's synced number is fresh."""
        return [] if self.synced_on(day) else self.scheduled_loans(day)

    def monthly_payment(self, day):
        return sum(loan_payment(l["principal"], l["annual_rate"], l["term_months"]) for l in self.scheduled_loans(day))

    def on(self, day):
        """Money in the account on a day (for cards and loans, what's owed). None if unknown."""
        a = self.account
        if a["opened"] and day[:7] < a["opened"]:
            return None
        if a["closed"] and day[:7] > a["closed"]:
            return 0.0
        loans = self.calculated_loans(day)
        if loans:
            return round(sum(
                loan_balance(l["principal"], l["annual_rate"], l["term_months"], l["start_date"], day) for l in loans
            ), 2)
        anchor, _ = self.since_anchor(day)
        if anchor is None:
            return None
        moved = self._through(self.cum_amount, day) - self._through(self.cum_amount, anchor[0])
        # Transactions are negative when money leaves: that shrinks an asset and grows a debt.
        return round(anchor[1] + (moved if self.side == "asset" else -moved), 2)


def load_ledgers(conn):
    """{account id: AccountLedger}, in the usual account order."""
    anchors, daily, loans, synced = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    for r in conn.execute("SELECT account_id, as_of, month, amount, source FROM balances"):
        anchors[r[0]].append((r[1] or month_end(r[2]), r[3]))
        if r[4] == "simplefin":
            synced[r[0]].append((r[1] or month_end(r[2]), r[3]))
    for r in conn.execute(
        "SELECT account_id, date, SUM(amount), COUNT(*) FROM transactions GROUP BY account_id, date ORDER BY account_id, date"
    ):
        daily[r[0]].append((r[1], r[2], r[3]))
    for r in conn.execute("SELECT * FROM loan_terms ORDER BY start_date"):
        loans[r["account_id"]].append(dict(r))
    return {
        a["id"]: AccountLedger(dict(a), anchors[a["id"]], daily[a["id"]], loans[a["id"]], synced[a["id"]])
        for a in conn.execute("SELECT * FROM accounts ORDER BY closed IS NOT NULL, sort, name")
    }
