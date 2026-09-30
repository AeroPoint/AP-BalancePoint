"""Business page: income, costs and profit per business account."""
from budget import business
from budget.csv_import import store_transactions

from conftest import category


def test_studio_report_counts_its_categories_anywhere(demo_conn):
    studio = next(b for b in business.businesses(demo_conn) if b["name"] == "Pat's Studio")
    before = business.report(demo_conn, studio)["all"]
    assert before["income"] > 0 and before["costs"] > 0 and before["put_in"] == 1500
    assert before["profit"] == round(before["income"] - before["costs"], 2)
    # A studio cost paid from the household card counts too, once it's in the studio's category.
    card = demo_conn.execute("SELECT id FROM accounts WHERE name = 'Rewards Card'").fetchone()[0]
    store_transactions(demo_conn, card, "x", [{"date": "2026-01-15", "amount": -40.0, "raw": "CAMERA STORE", "memo": None, "mcc": None}])
    demo_conn.execute("UPDATE transactions SET category_id = ?, category_source = 'manual' WHERE raw_description = 'CAMERA STORE'",
                      (category(demo_conn, "Studio Business"),))
    after = business.report(demo_conn, studio)["all"]
    assert round(after["costs"] - before["costs"], 2) == 40.0
