"""SimpleFIN Bridge sync against a fake Bridge on localhost (made-up accounts)."""
import base64
import json
import os
import stat
import threading
import time
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from budget import simplefin_import as sf
from budget.balances import load_ledgers

from conftest import account

TODAY = date.today()
REFRESHED = int(time.time()) - 3600  # the Bridge dates balances by when it last refreshed


def posted(days_ago, amount, desc, pending=False):
    d = TODAY - timedelta(days=days_ago)
    ts = int(datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc).timestamp()) + 18 * 3600
    return {"id": f"{desc}-{days_ago}", "posted": 0 if pending else ts, "amount": f"{amount:.2f}",
            "description": desc, "pending": pending}


class Bridge:
    def __init__(self):
        self.errlist, self.requests, self.fail = [], [], None
        self.accounts = [
            {"id": "ACT-CHK", "conn_id": "CON-BANK", "name": "Checking ...1111", "currency": "USD", "balance": "2500.00",
             "balance-date": REFRESHED, "transactions": [
                 posted(40, -12.00, "BEFORE SYNC FROM"), posted(3, -4.50, "PAT'S COFFEE"), posted(3, -4.50, "PAT'S COFFEE"),
                 posted(1, 2650.00, "ACME CORP PAYROLL"), posted(0, -9.99, "STILL PENDING", pending=True)]},
            {"id": "ACT-CARD", "conn_id": "CON-BANK", "name": "Card ...2222", "currency": "USD", "balance": "-512.34",
             "balance-date": REFRESHED, "transactions": [posted(2, -30.00, "SHELL OIL 12345")]},
            {"id": "ACT-401K", "conn_id": "CON-INV", "name": "401(k)", "currency": "USD", "balance": "54321.00",
             "balance-date": REFRESHED, "transactions": [posted(2, 700.00, "CONTRIBUTION")]},
            {"id": "ACT-LOAN", "conn_id": "CON-LOAN", "name": "Loan", "currency": "USD", "balance": "-365000.00",
             "balance-date": REFRESHED, "transactions": []},
            {"id": "ACT-NEW", "conn_id": "CON-INV", "name": "Brokerage", "currency": "USD", "balance": "100.00",
             "balance-date": REFRESHED, "transactions": []},
        ]
        self.connections = [{"conn_id": "CON-BANK", "org_name": "Test Bank"}, {"conn_id": "CON-INV", "org_name": "Invest Co"},
                            {"conn_id": "CON-LOAN", "org_name": "Loan Co"}]


@pytest.fixture
def bridge():
    state = Bridge()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(body.encode())

        def blocked(self):  # like the real Bridge's Cloudflare front door
            if self.headers.get("User-Agent", "").startswith("Python-urllib"):
                self.send(403, "error code: 1010", "text/plain")
                return True

        def do_POST(self):
            if self.blocked():
                return
            if self.path == "/claim/good":
                self.send(200, f"http://user:secret@127.0.0.1:{server.server_port}/simplefin", "text/plain")
            else:
                self.send(403, "Forbidden (was it already claimed?)", "text/plain")

        def do_GET(self):
            if self.blocked():
                return
            if self.headers.get("Authorization") != "Basic " + base64.b64encode(b"user:secret").decode():
                return self.send(403, "{}")
            if state.fail:
                return self.send(state.fail, "{}")
            q = parse_qs(urlsplit(self.path).query)
            start, end = int(q["start-date"][0]), int(q["end-date"][0])
            state.requests.append(datetime.fromtimestamp(start, timezone.utc).date())
            accounts = [{**a, "transactions": [t for t in a["transactions"] if t["pending"] or start <= t["posted"] < end]}
                        for a in state.accounts]
            self.send(200, json.dumps({"accounts": accounts, "connections": state.connections, "errlist": state.errlist}))

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state.url = f"http://user:secret@127.0.0.1:{server.server_port}/simplefin"
    state.token = lambda path: base64.b64encode(f"http://127.0.0.1:{server.server_port}{path}".encode()).decode()
    yield state
    server.shutdown()


@pytest.fixture
def mapped(conn):
    ids = {"chk": account(conn, "Checking"), "card": account(conn, "Card", "credit"),
           "401k": account(conn, "401k", "retirement"), "loan": account(conn, "Loan", "loan")}
    since = (TODAY - timedelta(days=5)).isoformat()
    sf.map_account(conn, "ACT-CHK", ids["chk"], sync_from=since)
    sf.map_account(conn, "ACT-CARD", ids["card"], sync_from=since)
    sf.map_account(conn, "ACT-401K", ids["401k"])
    sf.map_account(conn, "ACT-LOAN", ids["loan"])
    conn.execute("""INSERT INTO loan_terms (account_id, principal, annual_rate, term_months, start_date)
                    VALUES (?, 400000, 4.5, 360, '2020-06-01')""", (ids["loan"],))
    conn.commit()
    return ids


def test_claim_saves_a_private_access_url(bridge, tmp_path):
    with pytest.raises(sf.SimpleFinError, match="turned down"):
        sf.claim_setup_token(bridge.token("/claim/used"))
    url = sf.claim_setup_token(bridge.token("/claim/good") + "\n")  # stray line break from pasting
    sf.save_access_url(tmp_path, url)
    path = sf.access_url_path(tmp_path)
    assert sf.load_access_url(tmp_path) == url
    if os.name != "nt":  # only this user can read it (Windows has no such mode bits)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert sf.ACCESS_URL_FILE in (tmp_path / ".gitignore").read_text().splitlines()


def test_sync(bridge, conn, mapped):
    r = sf.sync(conn, bridge.url, TODAY)
    conn.commit()
    got = {a["label"]: (a["added"], a["ok"]) for a in r["accounts"]}
    assert got["Checking ...1111"] == (3, True)  # before sync_from and pending are skipped; same-day repeats kept
    assert got["401(k)"] == (0, True)  # balance only: contributions aren't income
    assert r["unmapped"] == [("ACT-NEW", "Invest Co", "Brokerage")]
    ledgers = load_ledgers(conn)
    assert ledgers[mapped["card"]].on(TODAY.isoformat()) == 512.34  # stored as what's owed
    assert ledgers[mapped["loan"]].on(TODAY.isoformat()) == 365000.0  # the lender's number beats the schedule
    assert conn.execute("SELECT as_of FROM balances WHERE account_id = ?", (mapped["chk"],)).fetchone()[0] == TODAY.isoformat()
    # again: nothing new
    assert all(a["added"] == 0 for a in sf.sync(conn, bridge.url, TODAY)["accounts"])


def test_outage_and_reauth_catch_up(bridge, conn, mapped):
    sf.sync(conn, bridge.url, TODAY)
    conn.execute("UPDATE simplefin_accounts SET last_synced = ? WHERE external_id = 'ACT-CARD'", ((TODAY - timedelta(days=20)).isoformat(),))
    bridge.errlist = [{"code": "gen.api", "msg": "Requested date range exceeds 45 days."},  # a notice, not a failure
                      {"code": "con.auth", "msg": "Sign in again", "conn_id": "CON-BANK"}]
    r = sf.sync(conn, bridge.url, TODAY)
    assert bridge.requests[-1] == TODAY - timedelta(days=25)  # pulled from the oldest last pull, less 5 days
    last = dict(conn.execute("SELECT external_id, last_synced FROM simplefin_accounts").fetchall())
    assert last["ACT-CARD"] == (TODAY - timedelta(days=20)).isoformat() and last["ACT-401K"] == TODAY.isoformat()
    assert any(s["account"] == "Card" for s in r["stale"])
    bridge.errlist = []
    sf.sync(conn, bridge.url, TODAY)
    assert conn.execute("SELECT last_synced FROM simplefin_accounts WHERE external_id = 'ACT-CARD'").fetchone()[0] == TODAY.isoformat()


def test_bridge_down_changes_nothing(bridge, conn, mapped):
    bridge.fail = 500
    with pytest.raises(sf.SimpleFinError):
        sf.sync(conn, bridge.url, TODAY)
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_a_later_typed_balance_wins(bridge, conn, mapped):
    conn.execute("INSERT INTO balances (account_id, month, amount, as_of, source) VALUES (?, ?, 2600, ?, 'manual')",
                 (mapped["chk"], TODAY.strftime("%Y-%m"), TODAY.isoformat()))
    sf.sync(conn, bridge.url, TODAY)
    assert conn.execute("SELECT amount FROM balances WHERE account_id = ?", (mapped["chk"],)).fetchone()[0] == 2600


def _ts(d, hour=18):
    return int(datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc).timestamp()) + hour * 3600


def test_month_end_pending_counts_in_that_month(bridge, conn):
    first = date(TODAY.year, TODAY.month, 1)
    last_day = first - timedelta(days=1)
    chk = account(conn, "Checking")
    sf.map_account(conn, "ACT-CHK", chk, sync_from=(first - timedelta(days=20)).isoformat())
    conn.commit()
    pend = lambda tid, amount, desc, d: {"id": tid, "posted": 0, "transacted_at": _ts(d), "amount": f"{amount:.2f}",  # noqa: E731
                                         "description": desc, "pending": True}
    bridge.accounts = [dict(bridge.accounts[0], transactions=[
        pend("P1", -42.10, "THAI BASIL SPRINGFIELD", last_day),   # dinner on the last day: tip added when it posts
        pend("P2", -250.00, "HOTEL HOLD", last_day),          # a hold that's released, never posts
        pend("P3", -9.00, "COFFEE TODAY", first),             # this month: left to post normally
    ])]
    r = sf.sync(conn, bridge.url, first)  # the 6:00 run on the 1st
    assert r["accounts"][0]["pending"] == 2
    rows = {t["raw_description"]: dict(t) for t in conn.execute("SELECT * FROM transactions WHERE pending = 1")}
    assert set(rows) == {"THAI BASIL SPRINGFIELD", "HOTEL HOLD"} and rows["HOTEL HOLD"]["effective_date"] == last_day.isoformat()
    # Running again doesn't duplicate them.
    assert sf.sync(conn, bridge.url, first)["accounts"][0]["pending"] == 0

    # Two days later dinner posts, with the tip, under a new id and a date in the new month.
    posted_day = first + timedelta(days=1)
    bridge.accounts = [dict(bridge.accounts[0], transactions=[
        {"id": "X1", "posted": _ts(posted_day), "transacted_at": _ts(last_day), "amount": "-50.52",
         "description": "THAI BASIL SPRINGFIELD", "pending": False},
        pend("P2", -250.00, "HOTEL HOLD", last_day),
    ])]
    r = sf.sync(conn, bridge.url, posted_day + timedelta(days=1))
    assert r["accounts"][0]["settled"] == 1
    dinner = conn.execute("SELECT * FROM transactions WHERE raw_description = 'THAI BASIL SPRINGFIELD'").fetchall()
    assert len(dinner) == 1 and dinner[0]["pending"] == 0 and dinner[0]["amount"] == -50.52
    assert dinner[0]["effective_date"] == last_day.isoformat()  # still counts in last month
    # The hold never posts: gone after two weeks, and not added back while the Bridge still lists it.
    r = sf.sync(conn, bridge.url, last_day + timedelta(days=sf.PENDING_KEEP_DAYS + 2))
    assert r["accounts"][0]["dropped"] == 1
    assert not conn.execute("SELECT 1 FROM transactions WHERE pending = 1").fetchone()
