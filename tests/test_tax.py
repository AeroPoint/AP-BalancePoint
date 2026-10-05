"""Tax time page: checkbox totals, business, income, accountant notes, and the CSV export."""
import csv
import io
from datetime import date

from budget import business, tax

from conftest import account, category, txn


def _flag(conn, name, label):
    conn.execute("UPDATE categories SET flag = ? WHERE name = ?", (label, name))


def test_default_year_is_last_year_until_may():
    assert tax.default_year(date(2026, 4, 30)) == 2025
    assert tax.default_year(date(2026, 5, 1)) == 2026


def test_checkbox_totals_count_on_the_effective_date(conn):
    acct = account(conn, "Joint Checking")
    _flag(conn, "Home & Garden", "Rental property")
    _flag(conn, "Mortgage & HOA", "Rental property")  # same label: one box
    _flag(conn, "Health & Fitness", "Should be FSA/HSA")
    txn(conn, acct, "2025-12-31", -100.0, "Home & Garden", flag="yes")
    txn(conn, acct, "2026-01-01", -40.0, "Home & Garden", flag="yes")
    txn(conn, acct, "2025-12-31", -7.0, "Home & Garden", flag="yes", date_override="2026-01-02")  # moved by hand
    txn(conn, acct, "2025-12-30", -900.0, "Mortgage & HOA", flag="yes")  # counts on the nearest 1st: 2026
    txn(conn, acct, "2025-06-01", -55.0, "Home & Garden")  # not ticked
    txn(conn, acct, "2025-06-02", -60.0, "Home & Garden", flag="check")
    txn(conn, acct, "2025-03-01", -25.0, "Health & Fitness", flag="yes")
    txn(conn, acct, "2026-03-01", -12.0, "Health & Fitness", flag="check")
    conn.commit()

    boxes, to_check = tax.checkboxes(conn, 2025)
    by = {b["label"]: b for b in boxes}
    assert by["Rental property"]["total"] == -100.0 and by["Rental property"]["n"] == 1
    assert by["Should be FSA/HSA"]["total"] == -25.0
    assert to_check == 1
    boxes, to_check = tax.checkboxes(conn, 2026)
    by = {b["label"]: b for b in boxes}
    assert by["Rental property"]["total"] == -947.0 and by["Rental property"]["n"] == 3
    assert {c["name"] for c in by["Rental property"]["categories"]} == {"Home & Garden", "Mortgage & HOA"}
    assert by["Should be FSA/HSA"]["n"] == 0 and to_check == 1


def test_page_statement_and_to_check_link(conn, client):
    acct = account(conn, "Joint Checking")
    _flag(conn, "Home & Garden", "Rental property")
    txn(conn, acct, "2025-04-01", -1234.0, "Home & Garden", flag="yes")
    txn(conn, acct, "2025-04-02", -10.0, "Home & Garden", flag="check")
    txn(conn, acct, "2025-04-03", -11.0, "Home & Garden", flag="check")
    conn.commit()
    page = client.get("/tax?year=2025").get_data(as_text=True)
    assert "Rental property <b>$1,234</b>" in page
    assert "/transactions?flag=check&amp;period=2025" in page and "<b>2</b> items still to check" in page
    assert "nothing left to check" in client.get("/tax?year=2024").get_data(as_text=True)


def test_business_profit_matches_the_business_page(demo_conn, client):
    year = date.today().year - 1
    data = tax.summary(demo_conn, year)
    studio = next(b for b in business.businesses(demo_conn) if b["name"] == "Pat's Studio")
    expected = dict(business.report(demo_conn, studio)["years"])[str(year)]
    got = next(b for b in data["businesses"] if b["account"]["name"] == "Pat's Studio")
    assert got["totals"]["profit"] == expected["profit"] and expected["profit"] > 0
    assert round(sum(r["amount"] for r in got["rows"]), 2) == expected["profit"]
    assert data["profit"] == expected["profit"]


def test_income_by_category_and_work_savings(demo_conn):
    data = tax.income(demo_conn, date.today().year - 1)
    names = {c["name"] for c in data["categories"]}
    assert {"Paycheck", "Rental Income", "Studio Income"} <= names
    assert data["work"] and data["work"]["paychecks"] == 24 and data["work"]["yours"] > 0


def test_notes_for_the_accountant(conn):
    acct = account(conn, "Joint Checking")
    txn(conn, acct, "2025-02-01", -10.0, notes="Ask the cpa about this")
    txn(conn, acct, "2025-02-02", -11.0, notes="Tax-deductible donation")
    txn(conn, acct, "2025-02-03", -12.0, notes="Taxi to the airport")  # not about taxes
    txn(conn, acct, "2025-02-04", -13.0, notes="Dinner with friends")
    txn(conn, acct, "2024-12-31", -14.0, notes="CPA question, last year")
    conn.commit()
    assert [r["amount"] for r in tax.notes(conn, 2025)] == [-10.0, -11.0]


def test_csv_export_columns_and_tricky_names(conn, client):
    acct = account(conn, 'Pat "Main", Checking')
    _flag(conn, "Home & Garden", "Rental property")
    txn(conn, acct, "2025-05-01", -80.5, "Home & Garden", name='Smith, "Pat" & Co', flag="yes",
        notes='Ask CPA: "repair", or improvement?')
    txn(conn, acct, "2025-05-02", 2000.0, "Paycheck", name="=HYPERLINK(1)")
    txn(conn, acct, "2025-05-03", -9.0, "Home & Garden", flag="check", notes="tax receipt")
    conn.commit()

    r = client.get("/tax/export.csv?year=2025")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    assert 'filename="tax-2025.csv"' in r.headers["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(r.get_data(as_text=True))))
    assert rows[0] == tax.CSV_COLUMNS
    body = rows[1:]
    assert ["Checkbox items", "2025-05-01", 'Pat "Main", Checking', 'Smith, "Pat" & Co', "Home & Garden", "-80.50",
            "Rental property", 'Ask CPA: "repair", or improvement?'] in body
    paycheck = next(row for row in body if row[0] == "Income")
    assert paycheck[3] == "'=HYPERLINK(1)" and paycheck[5] == "2000.00"  # never a formula in a spreadsheet
    notes = [row for row in body if row[0] == "Questions for your accountant"]
    assert len(notes) == 2 and "To check: Rental property" in [row[6] for row in notes]
    assert {row[0] for row in body} == {"Checkbox items", "Income", "Rental", "Questions for your accountant"}

    one = list(csv.reader(io.StringIO(client.get("/tax/export.csv?year=2025&section=checkbox").get_data(as_text=True))))
    assert len(one) == 2 and one[1][0] == "Checkbox items"
    assert len(list(csv.reader(io.StringIO(client.get("/tax/export.csv?year=2024&section=income").get_data(as_text=True))))) == 1


def test_pages_render_on_demo_and_empty(demo_conn, client):
    page = client.get(f"/tax?year={date.today().year - 1}").get_data(as_text=True)
    assert "Rental property" in page and "Pat&#39;s Studio" in page and "Ask the CPA" in page and "Paycheck" in page
    assert client.get("/tax").status_code == 200
    assert client.get("/tax?year=abc").status_code == 200
    assert client.get("/tax/export.csv?section=bogus").status_code == 200


def test_empty_database(client):
    page = client.get("/tax").get_data(as_text=True)
    assert "No category has a tax checkbox yet" in page and "No income recorded" in page
    assert client.get("/tax/export.csv").get_data(as_text=True).strip() == ",".join(tax.CSV_COLUMNS)


def test_questions_for_the_accountant(client, conn):
    """Written-down questions per tax year: add, answer, reopen, delete; in the CSV too."""
    client.post("/tax/questions?year=2025", data={"question": "Is the  supervision a business cost?"})
    client.post("/tax/questions?year=2026", data={"question": "Another year's question"})
    html = client.get("/tax?year=2025").get_data(as_text=True)
    assert "Is the supervision a business cost?" in html and "Another year" not in html
    qid = conn.execute("SELECT id FROM tax_questions WHERE year = 2025").fetchone()[0]
    client.post("/tax/questions?year=2025", data={"id": qid, "answer": "=Yes, both", "done": "1"})
    row = conn.execute("SELECT answer, done FROM tax_questions WHERE id = ?", (qid,)).fetchone()
    assert (row[0], row[1]) == ("=Yes, both", 1)
    assert "Answered (1)" in client.get("/tax?year=2025").get_data(as_text=True)
    csv_text = client.get("/tax/export.csv?year=2025&section=notes").get_data(as_text=True)
    assert "Is the supervision a business cost? | Answer: =Yes, both | (done)" in csv_text
    client.post("/tax/questions?year=2025", data={"id": qid, "done": "0"})
    assert tuple(conn.execute("SELECT done, answer FROM tax_questions WHERE id = ?", (qid,)).fetchone()) == (0, "=Yes, both")
    client.post("/tax/questions?year=2025", data={"id": qid, "answer": "", "done": "0"})
    assert conn.execute("SELECT answer FROM tax_questions WHERE id = ?", (qid,)).fetchone()[0] is None
    client.post("/tax/questions?year=2025", data={"id": qid, "delete": "1"})
    assert conn.execute("SELECT COUNT(*) FROM tax_questions WHERE year = 2025").fetchone()[0] == 0


def test_rental_nets_income_against_interest_escrow_and_ticked_costs(conn, client):
    """Ticked mortgage payments are split by the rental loan's schedule: interest and escrow count, principal doesn't."""
    from budget import balances
    chk = account(conn, "Checking")
    loan_acct = account(conn, "Rental Loan", "loan")
    conn.execute("""INSERT INTO loan_terms (account_id, label, principal, annual_rate, term_months, start_date)
                    VALUES (?, '$300,000 loan', 300000, 6.0, 360, '2020-01-01')""", (loan_acct,))
    loan_id = conn.execute("SELECT id FROM loan_terms").fetchone()[0]
    _flag(conn, "Home & Garden", "Rental property")
    _flag(conn, "Mortgage & HOA", "Rental property")
    pi = round(balances.loan_payment(300000, 6.0, 360), 2)
    for m in (1, 2):
        txn(conn, chk, f"2025-0{m}-05", -(pi + 400), "Mortgage & HOA", "Lender", flag="yes")
    txn(conn, chk, "2025-01-20", 2000, "Rental Income", "Rent")
    txn(conn, chk, "2025-02-20", 2000, "Rental Income", "Rent")
    txn(conn, chk, "2025-02-10", -150, "Home & Garden", "Hardware", flag="yes")
    txn(conn, chk, "2025-02-11", -99, "Home & Garden", "Not rental")  # not ticked: not a rental cost
    conn.commit()
    r = tax.rental(conn, 2025)
    assert (r["income"], r["paid"], r["other_costs"], r["loan"]) == (4000, round(2 * (pi + 400), 2), 150, None)
    assert r["deductible"] == round(2 * (pi + 400) + 150, 2)  # no loan picked: whole payments, and the page says so
    client.post("/tax/rental-loan?year=2025", data={"loan_id": loan_id})
    r = tax.rental(conn, 2025)
    i1, p1 = tax.split_payment(r["loan"], "2025-01-05")
    assert round(i1 + p1, 2) == pi and i1 > p1 > 0  # early in a 6% loan, mostly interest
    assert r["escrow"] == 800 and round(r["interest"] + r["principal"], 2) == round(2 * pi, 2)
    assert r["deductible"] == round(r["interest"] + 800 + 150, 2) and r["net"] == round(4000 - r["deductible"], 2)
    html = client.get("/tax?year=2025").get_data(as_text=True)
    assert "Net rental income" in html and "principal, which isn't a cost" in html and "rental net" in html
    body = list(csv.reader(io.StringIO(client.get("/tax/export.csv?year=2025&section=rental").get_data(as_text=True))))
    assert len(body) == 1 + 2 + 2 + 1  # header, rent x2, payments x2, hardware
