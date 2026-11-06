"""Amazon orders: what was in each Amazon charge, from Amazon's own order history.

Amazon's "Request Your Data" (Your Account, Request Your Data, Your Orders) sends a ZIP with one CSV
row per item: order id, order and ship dates, product name, quantity and what was owed for it. Upload
the ZIP (or the CSV in it) and each item is stored in amazon_items; then every Amazon charge is
matched to the items it paid for, so Categorize can show "Kids' socks, USB-C cable" instead of
"Amazon $45.60", suggest a category for products bought before, and start the Split form with each
category's share.

Amazon charges the card when a shipment leaves, so one charge is usually one shipment (the items of an
order that shipped together), sometimes a whole order or a few of its shipments at once. The charge
code in the bank text (AMAZON MKTPL*AB12CD34E) doesn't appear anywhere in Amazon's data, so matching
goes by amount and date: the items' total to the cent, the charge posted between the order date (a
few days before, at most) and 10 days after shipping, the one nearest the ship date winning. Some states add a delivery fee to the charge that the export
leaves out (Colorado's is under 50 cents), so a last pass allows a charge up to FEE_ALLOWANCE more than
its items when that same difference keeps turning up. Charges that don't match stay exactly as they were.

Only what matching and the pages need is kept: no addresses, payment details or gift messages.
"""
import csv
import io
import itertools
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

# Bank text of a charge Amazon could have itemized (not Prime, Kindle or other digital subscriptions,
# whose amounts wouldn't match anyway).
AMAZON_TEXT = re.compile(r"AMAZON|AMZN", re.IGNORECASE)
DAYS_BEFORE, DAYS_AFTER = 3, 10      # the bank's date: from a few days before the order to 10 after shipping
FEE_ALLOWANCE = 0.50                 # a charge may be this much over its items (state delivery fees),
FEE_REPEATS = 3                      # when that same amount over turns up on at least this many charges
MAX_SHIPMENTS = 8                    # orders with more shipments only match one shipment, or all of them
MAX_CSV_BYTES = 100 * 1024 * 1024    # per file read out of the ZIP
ORDER_FILE = re.compile(r"order[ _.]?history.*\.csv$", re.IGNORECASE)
MISSING = {"", "not available", "not applicable", "n/a"}


class AmazonFormatError(ValueError):
    pass


@dataclass
class Item:
    order_id: str
    order_date: str | None
    ship_date: str | None
    shipment: str          # tracking number(s), so items that shipped together group together
    product: str
    asin: str
    quantity: int
    total: float           # what was owed for this line: price, tax, shipping and discounts


@dataclass
class Shipment:
    order_id: str
    day: date
    items: list = field(default_factory=list)

    @property
    def total(self):
        return round(sum(i["total"] for i in self.items), 2)


def _money(value):
    s = str(value or "").strip().strip("'\"").replace("$", "").replace(",", "")
    if s.lower() in MISSING:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _day(value):
    """The latest of Amazon's UTC timestamps ("2026-01-15T18:22:33Z and 2026-01-16T...") as a local
    date, the way the bank would date it."""
    days = []
    for part in str(value or "").split(" and "):
        part = part.strip()
        if part.lower() in MISSING:
            continue
        try:
            stamp = datetime.fromisoformat(part.replace("Z", "+00:00"))
        except ValueError:
            try:
                stamp = datetime.strptime(part, "%m/%d/%Y")
            except ValueError:
                continue
        days.append((stamp.astimezone() if stamp.tzinfo else stamp).date())
    return max(days).isoformat() if days else None


def _column(header, *names):
    wanted = {n.lower() for n in names}
    for i, h in enumerate(header):
        if h.strip().strip('"').lower() in wanted:
            return i
    return None


def parse_orders(text):
    """Items from Amazon's order history CSV. Cancelled lines and lines with nothing owed are left out."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows:
        raise AmazonFormatError("The file is empty.")
    header = rows[0]
    col = {
        "order_id": _column(header, "Order ID"),
        "order_date": _column(header, "Order Date"),
        "ship_date": _column(header, "Ship Date"),
        "product": _column(header, "Product Name"),
        "total": _column(header, "Total Owed", "Total Amount"),  # "Total Amount" in exports from 2026 on
        "quantity": _column(header, "Quantity", "Original Quantity"),
        "asin": _column(header, "ASIN"),
        "status": _column(header, "Order Status"),
        "tracking": _column(header, "Carrier Name & Tracking Number"),
    }
    if None in (col["order_id"], col["product"], col["total"]) or (col["order_date"] is None and col["ship_date"] is None):
        raise AmazonFormatError(
            "This doesn't look like Amazon's order history (it needs Order ID, Product Name, Total Owed or "
            "Total Amount, and a date). "
            "Upload the ZIP from Request Your Data, or the Order History CSV inside it.")
    get = lambda row, key: row[col[key]].strip() if col[key] is not None and col[key] < len(row) else ""
    items = []
    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue
        total = _money(get(row, "total"))
        if get(row, "status").lower() == "cancelled" or not get(row, "order_id") or total == 0:
            continue
        try:
            quantity = max(int(float(get(row, "quantity") or 1)), 1)
        except ValueError:
            quantity = 1
        order_date = _day(get(row, "order_date"))
        items.append(Item(
            order_id=get(row, "order_id"), order_date=order_date, ship_date=_day(get(row, "ship_date")) or order_date,
            shipment=get(row, "tracking") if get(row, "tracking").lower() not in MISSING else "",
            product=" ".join(get(row, "product").split()) or "(no name)", asin=get(row, "asin"),
            quantity=quantity, total=round(total, 2)))
    return items


def read_upload(filename, content):
    """Items from an uploaded ZIP (every order history CSV in it) or a single CSV."""
    if filename.lower().endswith(".zip") or content[:2] == b"PK":
        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile as exc:
            raise AmazonFormatError("That ZIP file couldn't be opened.") from exc
        names = [n for n in archive.namelist() if ORDER_FILE.search(n.rsplit("/", 1)[-1]) and "digital" not in n.lower()]
        if not names:
            raise AmazonFormatError("There's no order history in that ZIP. Request “Your Orders” from Amazon's "
                                    "Request Your Data page; its ZIP has a file like Order History.csv.")
        items = []
        for name in names:
            if archive.getinfo(name).file_size > MAX_CSV_BYTES:
                raise AmazonFormatError(f"{name} is too large to read.")
            items += parse_orders(archive.read(name).decode("utf-8-sig", errors="replace"))
        return items
    return parse_orders(content.decode("utf-8-sig", errors="replace"))


def store_items(conn, items):
    """Save items. A later export replaces an order's unmatched items and adds any new ones (a shipment
    that left since), while items already matched to a charge stay as they are, so a new export never
    undoes a match. Returns (orders read, items added)."""
    by_order = {}
    for item in items:
        by_order.setdefault(item.order_id, []).append(item)
    key = lambda i: (i["product"], i["asin"], round(i["total"], 2), i["ship_date"], i["shipment"])
    added = 0
    for order_id, lines in by_order.items():
        kept = {}
        for r in conn.execute("SELECT * FROM amazon_items WHERE order_id = ? AND transaction_id IS NOT NULL", (order_id,)):
            kept[key(r)] = kept.get(key(r), 0) + 1
        conn.execute("DELETE FROM amazon_items WHERE order_id = ? AND transaction_id IS NULL", (order_id,))
        fresh = []
        for i in lines:
            k = key(vars(i))
            if kept.get(k):
                kept[k] -= 1  # already stored, and matched
                continue
            fresh.append((i.order_id, i.order_date, i.ship_date, i.shipment, i.product, i.asin, i.quantity, i.total))
        conn.executemany(
            """INSERT INTO amazon_items (order_id, order_date, ship_date, shipment, product, asin, quantity, total)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", fresh)
        added += len(fresh)
    return len(by_order), added


def _charges(conn):
    """Amazon charges with no items yet: (id, bank date, amount owed), the whole charge if it's split."""
    return [
        (r["id"], date.fromisoformat(r["date"]), round(-r["amount"], 2))
        for r in conn.execute(
            """SELECT id, date, COALESCE(split_total, amount) AS amount, raw_description FROM transactions t
               WHERE split_of IS NULL AND pending = 0 AND COALESCE(split_total, amount) < 0
                 AND NOT EXISTS (SELECT 1 FROM amazon_items a WHERE a.transaction_id = t.id)""")
        if AMAZON_TEXT.search(r["raw_description"])
    ]


def _candidates(conn):
    """Every way unmatched items could have been charged: one shipment, a few of an order's shipments,
    or the whole order. [(items' total, latest ship day, [item ids], number of shipments, order day)]"""
    shipments = {}
    for r in conn.execute("SELECT * FROM amazon_items WHERE transaction_id IS NULL AND ship_date IS NOT NULL"):
        key = (r["order_id"], r["ship_date"], r["shipment"])
        shipments.setdefault(key, Shipment(r["order_id"], date.fromisoformat(r["ship_date"]))).items.append(r)
    ordered = {r[0]: date.fromisoformat(r[1]) for r in conn.execute(
        "SELECT order_id, MIN(COALESCE(order_date, ship_date)) FROM amazon_items GROUP BY order_id")}
    orders = {}
    for s in shipments.values():
        orders.setdefault(s.order_id, []).append(s)
    out = []
    for order_id, group in orders.items():
        sizes = range(1, len(group) + 1) if len(group) <= MAX_SHIPMENTS else (1, len(group))
        for n in sizes:
            for combo in itertools.combinations(group, n):
                out.append((round(sum(s.total for s in combo), 2), max(s.day for s in combo),
                            [i["id"] for s in combo for i in s.items], n, ordered.get(order_id, combo[0].day)))
    return out


def match(conn):
    """Link unmatched Amazon charges to the items they paid for. Returns how many charges matched.

    Passes, each claiming items before the next: exact amount on one shipment, exact amount on several
    shipments of one order, then a charge a few cents over a shipment or order, for a delivery fee.
    A real fee is the same few amounts over and over (Colorado's has been 27 to 31 cents), while
    chance near-misses are scattered, so the last pass takes only amounts over that turn up again and
    again (FEE_REPEATS). Within a pass, the charge whose date is nearest a candidate's ship date goes
    first, then the commoner fee.
    """
    charges = _charges(conn)
    candidates = _candidates(conn)
    by_cents = {}
    for n, cand in enumerate(candidates):
        by_cents.setdefault(round(cand[0] * 100), []).append(n)
    passes = [  # (cents the charge may be over its items, which candidates)
        ((0,), lambda cand: cand[3] == 1),
        ((0,), lambda cand: cand[3] > 1),
        (range(1, round(FEE_ALLOWANCE * 100) + 1), lambda cand: True),
    ]
    used, matched = set(), set()
    for overs, fits in passes:
        pairs = []
        for tid, day, amount in charges:
            if tid in matched:
                continue
            for over in overs:
                for n in by_cents.get(round(amount * 100) - over, ()):
                    cand = candidates[n]
                    gap = (day - cand[1]).days
                    # Usually charged when it ships, sometimes when it's ordered (preorders, some sellers).
                    if (cand[4] - day).days <= DAYS_BEFORE and gap <= DAYS_AFTER and fits(cand) and not used.intersection(cand[2]):
                        # Nearest the ship date first; after shipping beats the same distance before it.
                        pairs.append((abs(gap) + (0.5 if gap < 0 else 0), over, tid, n))
        seen = {}
        for _, over, tid, _ in pairs:
            seen.setdefault(over, set()).add(tid)
        common = {over: len(tids) for over, tids in seen.items()}
        if overs != (0,):
            top = max(common.values(), default=0)
            common = {over: k for over, k in common.items() if k >= max(FEE_REPEATS, top * 0.1)}
        for _, _, tid, n in sorted((p for p in pairs if p[1] in common), key=lambda p: (p[0], -common[p[1]])):
            ids = candidates[n][2]
            if tid in matched or used.intersection(ids):
                continue
            conn.executemany("UPDATE amazon_items SET transaction_id = ? WHERE id = ?", [(tid, i) for i in ids])
            used.update(ids)
            matched.add(tid)
    return len(matched)


def import_upload(conn, filename, content):
    """Read, store and match. Returns a dict for the page or the command line."""
    items = read_upload(filename, content)
    if not items:
        raise AmazonFormatError("No orders in that file.")
    orders, stored = store_items(conn, items)
    matched = match(conn)
    left = len(_charges(conn))
    first = min((i.order_date or i.ship_date for i in items if i.order_date or i.ship_date), default=None)
    last = max((i.ship_date or i.order_date for i in items if i.order_date or i.ship_date), default=None)
    return {"items": len(items), "orders": orders, "stored": stored, "matched": matched,
            "unmatched": left, "first": first, "last": last}


def items_for(conn, ids):
    """{transaction id: [item rows]} for these transactions (a split part shows its whole charge's items)."""
    ids = list(ids)
    if not ids:
        return {}
    marks = ", ".join("?" * len(ids))
    parent = {r["id"]: r["parent"] for r in conn.execute(
        f"SELECT id, COALESCE(split_of, id) AS parent FROM transactions WHERE id IN ({marks})", ids)}
    parents = set(parent.values())
    if not parents:
        return {}
    marks = ", ".join("?" * len(parents))
    by_parent = {}
    for r in conn.execute(f"SELECT * FROM amazon_items WHERE transaction_id IN ({marks}) ORDER BY total DESC, id",
                          tuple(parents)):
        by_parent.setdefault(r["transaction_id"], []).append(r)
    return {tid: by_parent[p] for tid, p in parent.items() if p in by_parent}


def item_label(item):
    name = item["product"]
    name = name if len(name) <= 70 else name[:67].rstrip() + "…"
    return f"{item['quantity']}× {name}" if item["quantity"] > 1 else name


class Suggester:
    """Suggests an item's category from earlier purchases of the same product (its ASIN).

    A charge with one category tells us every item on it belongs there; a split charge, which of its
    items were in each part when an item's amount is exactly one part. A product counts only when
    every earlier purchase of it went to the same category: tried on one household's years of
    Amazon history, guessing from similar product names, or from where a product usually went, was
    wrong about as often as right, while a product always sorted the same way was a fair bet.
    """

    def __init__(self, conn):
        rows = conn.execute(
            """SELECT a.asin, a.total, t.id AS tid, t.category_id, t.split_total FROM amazon_items a
               JOIN transactions t ON t.id = a.transaction_id
               WHERE a.asin != '' AND t.category_id IS NOT NULL AND t.category_source != 'none'""").fetchall()
        parts = {}
        for r in conn.execute(
                """SELECT COALESCE(split_of, id) AS parent, amount, category_id FROM transactions
                   WHERE split_of IS NOT NULL OR split_total IS NOT NULL"""):
            parts.setdefault(r["parent"], []).append((round(-r["amount"], 2), r["category_id"]))
        seen = {}
        for r in rows:
            category_id = r["category_id"]
            if r["split_total"] is not None:
                category_id = next((c for amt, c in parts.get(r["tid"], []) if abs(amt - r["total"]) < 0.005), None)
            seen.setdefault(r["asin"], set()).add(category_id)
        self.by_asin = {asin: next(iter(cats)) for asin, cats in seen.items() if len(cats) == 1 and None not in cats}

    def category(self, item):
        return self.by_asin.get(item["asin"])

    def for_charge(self, items, total):
        """(one category for the whole charge or None, split lines or []) for an uncategorized charge.

        Split lines group the items by suggested category with their amounts; the last line is left
        blank so it takes whatever the items don't cover (a delivery fee, rounding).
        """
        if not items:
            return None, []
        guesses = [(self.category(i), i["total"]) for i in items]
        cats = {c for c, _ in guesses if c}
        if len(cats) <= 1:
            return next(iter(cats), None), []  # items it doesn't know don't stand in the way
        shares = {}
        for c, amount in guesses:
            shares[c] = round(shares.get(c, 0) + amount, 2)
        if any(amount <= 0 for amount in shares.values()):
            return None, []  # a discount bigger than its items: leave this one to you
        order = sorted(shares, key=lambda c: (c is None, -shares[c]))
        lines = [{"category_id": c, "amount": shares[c]} for c in order]
        lines[-1]["amount"] = None
        return None, lines
