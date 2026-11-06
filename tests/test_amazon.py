"""Amazon order history: reading the export, matching items to Amazon charges, and what the pages show
(made-up orders and charges)."""
import csv
import io
import zipfile

import pytest

from budget import amazon, splits

from conftest import account, category, txn

HEADER = ["Website", "Order ID", "Order Date", "Purchase Order Number", "Currency", "Unit Price", "Unit Price Tax",
          "Shipping Charge", "Total Discounts", "Total Owed", "Shipment Item Subtotal", "Shipment Item Subtotal Tax",
          "ASIN", "Product Condition", "Quantity", "Payment Instrument Type", "Order Status", "Shipment Status",
          "Ship Date", "Shipping Option", "Shipping Address", "Billing Address", "Carrier Name & Tracking Number",
          "Product Name", "Gift Message", "Gift Sender Name", "Gift Recipient Contact Details", "Item Serial Number"]


def line(order, ordered, shipped, product, total, asin="B000000001", qty=1, tracking="AMZN_US(TBA000000001)",
         status="Closed"):
    row = dict.fromkeys(HEADER, "Not Available")
    row.update({"Website": "Amazon.com", "Order ID": order, "Order Date": f"{ordered}T12:00:00Z", "Currency": "USD",
                "Total Owed": total, "ASIN": asin, "Quantity": str(qty), "Order Status": status,
                "Ship Date": f"{shipped}T12:00:00Z" if shipped else "Not Available",
                "Carrier Name & Tracking Number": tracking, "Product Name": product,
                "Shipping Address": "Pat Smith 1 Example St", "Payment Instrument Type": "Visa - 0000"})
    return row


def export(*rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, HEADER, quoting=csv.QUOTE_ALL)
    writer.writeheader()
    writer.writerows(rows)
    return "﻿" + out.getvalue()


def zipped(text, name="Your Orders/Order History.csv"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text)
        z.writestr("Your Orders/Digital Content Orders.csv", "nothing,here\n")
    return buf.getvalue()


@pytest.fixture
def card(conn):
    acct = account(conn, "Card", "credit")
    conn.execute("UPDATE rules SET leave_uncategorized = 1, allow_split = 1 WHERE rename_to = 'Amazon'")
    conn.commit()
    return acct


def charge(conn, card, day, amount, raw="AMAZON MKTPL*AB12CD34E AMZN.COM/BILL WA"):
    return txn(conn, card, day, amount, name="Amazon", raw_description=raw)


def linked(conn, tid):
    return sorted(r[0] for r in conn.execute("SELECT product FROM amazon_items WHERE transaction_id = ?", (tid,)))


def test_reads_the_export_skipping_cancelled_and_free_lines():
    items = amazon.parse_orders(export(
        line("111-0000001", "2026-03-01", "2026-03-02", "Toddler socks, 6 pack", "12.34", qty=2),
        line("111-0000002", "2026-03-01", None, "Never shipped", "5.00"),
        line("111-0000003", "2026-03-01", "2026-03-02", "Cancelled thing", "9.99", status="Cancelled"),
        line("111-0000004", "2026-03-01", "2026-03-02", "Free sample", "0"),
    ))
    assert [(i.order_id, i.product, i.quantity, i.total, i.ship_date) for i in items] == [
        ("111-0000001", "Toddler socks, 6 pack", 2, 12.34, "2026-03-02"),
        ("111-0000002", "Never shipped", 1, 5.0, "2026-03-01"),  # not shipped yet: dated by the order
    ]


def test_reads_the_newer_layout():
    """Exports from 2026 sort the columns by name, say Total Amount and Original Quantity."""
    text = ("ASIN,Carrier Name & Tracking Number,Order Date,Order ID,Order Status,Original Quantity,Product Name,"
            "Ship Date,Shipping Address,Total Amount,Unit Price\n"
            "B000000009,USPS(9400000000000000000001),2026-03-01T12:00:00Z,111-0000009,Closed,3,Crayons,"
            "2026-03-02T12:00:00Z,Pat Smith 1 Example St,10.74,3.33\n")
    item, = amazon.parse_orders(text)
    assert (item.product, item.quantity, item.total, item.ship_date) == ("Crayons", 3, 10.74, "2026-03-02")


def test_a_file_that_isnt_an_order_history_is_refused():
    with pytest.raises(amazon.AmazonFormatError):
        amazon.parse_orders("Date,Description,Amount\n2026-03-01,Coffee,-4.00\n")
    with pytest.raises(amazon.AmazonFormatError):
        amazon.read_upload("orders.zip", zipped("x", name="Something Else.csv"))


def test_matches_one_shipment_several_shipments_and_a_delivery_fee(conn, card):
    one = charge(conn, card, "2026-03-03", -20.00)
    both = charge(conn, card, "2026-03-12", -30.00)
    fees = [charge(conn, card, f"2026-03-{d}", -(amount + 0.29)) for d, amount in (("20", 15.00), ("22", 4.00), ("24", 6.00))]
    early = charge(conn, card, "2026-01-24", -259.38)  # charged at order time, with the same fee
    stray = charge(conn, card, "2026-03-26", -9.07)    # 7 cents over an order: a coincidence, not a fee
    far = charge(conn, card, "2026-05-01", -7.50)      # weeks after its order shipped: not that order
    coffee = txn(conn, card, "2026-03-03", -20.00, name="Pat's Coffee", raw_description="PATS COFFEE")
    got = amazon.import_upload(conn, "Order History.csv", export(
        line("111-0000001", "2026-03-01", "2026-03-02", "Board book", "12.00"),
        line("111-0000001", "2026-03-01", "2026-03-02", "Crayons", "8.00"),
        line("111-0000002", "2026-03-08", "2026-03-09", "Dish soap", "10.00", tracking="AMZN_US(TBA2)"),
        line("111-0000002", "2026-03-08", "2026-03-10", "Sponges", "20.00", tracking="AMZN_US(TBA3)"),
        line("111-0000003", "2026-03-18", "2026-03-19", "Garden gloves", "15.00"),
        line("111-0000005", "2026-03-20", "2026-03-21", "Glue sticks", "4.00"),
        line("111-0000006", "2026-03-22", "2026-03-23", "Tape", "6.00"),
        line("111-0000007", "2026-03-24", "2026-03-25", "Stickers", "9.00"),
        line("111-0000004", "2026-03-18", "2026-03-19", "Twine", "7.50"),
        line("111-0000020", "2026-01-23", "2026-01-30", "Textbook", "259.09"),
    ).encode())
    assert got["matched"] == 6 and got["unmatched"] == 2
    assert linked(conn, one) == ["Board book", "Crayons"]
    assert linked(conn, both) == ["Dish soap", "Sponges"]
    assert [linked(conn, f) for f in fees] == [["Garden gloves"], ["Glue sticks"], ["Tape"]]
    assert linked(conn, early) == ["Textbook"]
    assert linked(conn, stray) == [] and linked(conn, far) == [] and linked(conn, coffee) == []


def test_the_charge_nearest_the_ship_date_gets_the_items(conn, card):
    early = charge(conn, card, "2026-04-02", -25.00)
    late = charge(conn, card, "2026-04-11", -25.00)
    amazon.import_upload(conn, "o.csv", export(
        line("111-0000010", "2026-04-08", "2026-04-09", "Late order", "25.00"),
        line("111-0000011", "2026-03-31", "2026-04-01", "Early order", "25.00"),
    ).encode())
    assert linked(conn, early) == ["Early order"]
    assert linked(conn, late) == ["Late order"]


def test_a_newer_export_keeps_matches_and_adds_new_shipments(conn, card):
    first = charge(conn, card, "2026-03-03", -12.00)
    amazon.import_upload(conn, "a.csv", export(
        line("111-0000001", "2026-03-01", "2026-03-02", "Board book", "12.00"),
        line("111-0000001", "2026-03-01", None, "Backordered puzzle", "9.00", tracking="Not Available"),
    ).encode())
    second = charge(conn, card, "2026-03-16", -9.00)
    got = amazon.import_upload(conn, "b.zip", zipped(export(
        line("111-0000001", "2026-03-01", "2026-03-02", "Board book", "12.00"),
        line("111-0000001", "2026-03-01", "2026-03-15", "Backordered puzzle", "9.00", tracking="AMZN_US(TBA9)"),
    )))
    assert got["matched"] == 1
    assert linked(conn, first) == ["Board book"] and linked(conn, second) == ["Backordered puzzle"]
    assert conn.execute("SELECT COUNT(*) FROM amazon_items").fetchone()[0] == 2


def test_a_split_charge_still_matches_by_its_whole_amount(conn, card):
    tid = charge(conn, card, "2026-03-03", -20.00)
    splits.split(conn, tid, [(category(conn, "Groceries"), 5.00), (category(conn, "Shopping & Personal"), None)])
    amazon.import_upload(conn, "o.csv", export(line("111-0000001", "2026-03-01", "2026-03-02", "Snacks", "20.00")).encode())
    assert linked(conn, tid) == ["Snacks"]


def test_categorize_shows_items_and_suggests_for_products_bought_before(conn, card, client):
    groceries, home = category(conn, "Groceries"), category(conn, "Shopping & Personal")
    old = charge(conn, card, "2026-02-03", -9.00)
    old_mixed = charge(conn, card, "2026-02-10", -30.00)
    new = charge(conn, card, "2026-03-03", -11.00)
    mixed = charge(conn, card, "2026-03-12", -40.00)
    amazon.import_upload(conn, "o.csv", export(
        line("111-0000001", "2026-02-01", "2026-02-02", "Organic Peanut Butter Crunchy Jar", "9.00", asin="B0PB"),
        line("111-0000002", "2026-02-08", "2026-02-09", "Lavender Dish Soap Refill Bottle", "20.00", asin="B0DS"),
        line("111-0000002", "2026-02-08", "2026-02-09", "Whole Bean Dark Roast Coffee", "10.00", asin="B0CF"),
        line("111-0000003", "2026-03-01", "2026-03-02", "Organic Peanut Butter Smooth Jar", "11.00", asin="B0PB"),
        line("111-0000004", "2026-03-10", "2026-03-11", "Lavender Dish Soap Refill Bottle", "25.00", asin="B0DS"),
        line("111-0000004", "2026-03-10", "2026-03-11", "Whole Bean Dark Roast Coffee", "15.00", asin="B0CF"),
    ).encode())
    conn.execute("UPDATE transactions SET category_id = ?, category_source = 'manual' WHERE id = ?", (groceries, old))
    splits.split(conn, old_mixed, [(home, 20.00), (groceries, 10.00)])
    conn.commit()

    page = client.get("/categorize").get_data(as_text=True)
    assert "Organic Peanut Butter Smooth Jar ($11.00)" in page
    assert f'data-category="{groceries}"' in page  # one tap: Use Groceries
    # The mixed one starts its Split form with each category's share, the last line taking what's left (a fee, rounding).
    form = next(f for f in splits_forms(page) if f["id"] == mixed)
    assert form["lines"] == [{"category_id": home, "amount": 25.0}, {"category_id": groceries, "amount": None}]

    assert "Lavender Dish Soap" in client.get("/transactions?q=lavender").get_data(as_text=True)


def splits_forms(page):
    import html
    import json
    import re

    return [json.loads(html.unescape(m)) for m in re.findall(r'data-split="([^"]+)"', page)]


def test_upload_page_and_forgetting(conn, card, client):
    charge(conn, card, "2026-03-03", -12.00)
    conn.commit()
    data = {"orders": (io.BytesIO(zipped(export(line("111-0000001", "2026-03-01", "2026-03-02", "Board book", "12.00")))),
                       "Your Orders.zip")}
    response = client.post("/upload/amazon", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert "Matched 1 more Amazon charges" in response.get_data(as_text=True)
    assert "1 items in 1 orders" in client.get("/upload").get_data(as_text=True)
    client.post("/upload/amazon/forget")
    assert conn.execute("SELECT COUNT(*) FROM amazon_items").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1


def test_no_suggestion_for_a_product_sorted_two_ways(conn, card):
    groceries, shopping = category(conn, "Groceries"), category(conn, "Shopping & Personal")
    a, b, new = (charge(conn, card, d, -5.00) for d in ("2026-01-03", "2026-02-03", "2026-03-03"))
    amazon.import_upload(conn, "o.csv", export(
        *(line(f"111-000003{n}", d, d, "Protein bars", "5.00", asin="B0BAR")
          for n, d in enumerate(("2026-01-02", "2026-02-02", "2026-03-02")))).encode())
    conn.execute("UPDATE transactions SET category_id = ?, category_source = 'manual' WHERE id = ?", (groceries, a))
    conn.execute("UPDATE transactions SET category_id = ?, category_source = 'manual' WHERE id = ?", (shopping, b))
    items = amazon.items_for(conn, [new])
    assert amazon.Suggester(conn).for_charge(items[new], 5.00) == (None, [])
