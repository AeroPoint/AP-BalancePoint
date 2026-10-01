"""The Bank sync page, against the fake SimpleFIN Bridge on localhost (made-up accounts)."""
from datetime import date, timedelta
from pathlib import Path

from budget import simplefin_import as sf

from conftest import account
from test_simplefin import bridge  # noqa: F401  (the fake Bridge fixture)


def data_dir(app):
    return Path(app.config["DATABASE"]).parent


def test_page_renders_with_nothing_connected(client):
    r = client.get("/bank-sync")
    assert r.status_code == 200
    assert b"Setup token" in r.data and b"bridge.simplefin.org" in r.data
    assert b"Sync now" not in r.data


def test_bad_token_is_a_friendly_error(client, bridge):  # noqa: F811
    r = client.post("/bank-sync/connect", data={"token": "not a token!"}, follow_redirects=True)
    assert b"doesn&#39;t look like a SimpleFIN setup token" in r.data
    r = client.post("/bank-sync/connect", data={"token": bridge.token("/claim/used")}, follow_redirects=True)
    assert b"turned down the setup token" in r.data


def test_connect_map_sync_unmap_disconnect(app, client, conn, bridge):  # noqa: F811
    checking, k401 = account(conn, "Checking"), account(conn, "401k", "retirement")
    conn.commit()

    # Connect: the token is claimed, the access saved where run.py looks, and the accounts listed once.
    r = client.post("/bank-sync/connect", data={"token": bridge.token("/claim/good")}, follow_redirects=True)
    assert b"Connected." in r.data
    assert sf.load_access_url(data_dir(app)) == bridge.url
    assert len(bridge.requests) == 1
    for name in (b"Checking ...1111", b"Card ...2222", b"Brokerage", b"Test Bank"):
        assert name in r.data
    assert b"secret" not in r.data  # the access URL is a credential: never on the page

    # Viewing the page again doesn't spend a Bridge request.
    client.get("/bank-sync")
    assert len(bridge.requests) == 1

    # Map: blank date and "by account type" default the way run.py simplefin-map does.
    since = (date.today() - timedelta(days=1)).isoformat()
    client.post("/bank-sync/map", data={"external_id": "ACT-CHK", "account_id": checking, "mode": "auto", "sync_from": since})
    client.post("/bank-sync/map", data={"external_id": "ACT-401K", "account_id": k401, "mode": "auto", "sync_from": ""})
    rows = {r["external_id"]: dict(r) for r in conn.execute("SELECT * FROM simplefin_accounts")}
    assert rows["ACT-CHK"]["transactions"] == 1 and rows["ACT-CHK"]["sync_from"] == since
    assert rows["ACT-401K"]["transactions"] == 0 and rows["ACT-401K"]["sync_from"] == date.today().isoformat()
    assert rows["ACT-CHK"]["org"] == "Test Bank"
    r = client.get("/bank-sync")
    assert b"Not pulled yet" in r.data and b"STALE" in r.data

    # Sync now: the CLI's summary, and each account's last pull.
    bridge.errlist = [{"code": "gen.api", "msg": "A notice from the Bridge"}]
    r = client.post("/bank-sync/sync", follow_redirects=True)
    assert len(bridge.requests) == 2
    assert b"Synced: 1 new transaction." in r.data  # only today's payroll is on or after sync_from
    assert b"Checking: 1 new of 1, balance 2,500.00" in r.data
    assert b"Bridge says: A notice from the Bridge [gen.api]" in r.data
    assert b"Not mapped yet: Test Bank - Card ...2222" in r.data
    assert b"Last pulled" in r.data
    status = {s["external_id"]: s for s in sf.status(conn)}
    assert status["ACT-CHK"]["last_synced"] == date.today().isoformat() and not status["ACT-CHK"]["stale"]
    assert b"STALE" not in client.get("/bank-sync").data

    # Unmap: one directly, one by picking "Don't sync".
    client.post("/bank-sync/unmap", data={"external_id": "ACT-401K"})
    client.post("/bank-sync/map", data={"external_id": "ACT-CHK", "account_id": ""})
    assert conn.execute("SELECT COUNT(*) FROM simplefin_accounts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1  # what came in stays

    # Disconnect removes the access file.
    client.post("/bank-sync/disconnect")
    assert not sf.access_url_path(data_dir(app)).exists()
    assert b"Setup token" in client.get("/bank-sync").data


def test_mode_change_keeps_the_start_date(client, conn, bridge, app):  # noqa: F811
    checking = account(conn, "Checking")
    conn.commit()
    sf.save_access_url(data_dir(app), bridge.url)
    client.post("/bank-sync/map", data={"external_id": "ACT-CHK", "account_id": checking, "sync_from": "2026-01-15"})
    client.post("/bank-sync/map", data={"external_id": "ACT-CHK", "account_id": checking, "mode": "balance", "sync_from": ""})
    row = conn.execute("SELECT transactions, sync_from FROM simplefin_accounts").fetchone()
    assert (row[0], row[1]) == (0, "2026-01-15")
    # Mapped from the command line and never listed here: still shown, with a way to stop it.
    r = client.get("/bank-sync")
    assert b"Not in the latest list" in r.data and b"/bank-sync/unmap" in r.data
    assert bridge.requests == []


def test_sync_failure_changes_nothing(client, conn, bridge, app):  # noqa: F811
    sf.save_access_url(data_dir(app), bridge.url)
    sf.map_account(conn, "ACT-CHK", account(conn, "Checking"), sync_from="2020-01-01")
    conn.commit()
    bridge.fail = 500
    r = client.post("/bank-sync/sync", follow_redirects=True)
    assert b"Sync failed, nothing changed" in r.data
    assert conn.execute("SELECT last_synced FROM simplefin_accounts").fetchone()[0] is None


def test_log_tail_never_shows_a_login(client, app):
    lines = [f"line {i}" for i in range(40)] + ["Couldn't reach https://user:hunter2@bridge.example.com/simplefin"]
    (data_dir(app) / "simplefin-sync.log").write_text("\n".join(lines) + "\n")
    r = client.get("/bank-sync")
    assert b"line 39" in r.data and b"line 10" not in r.data
    assert b"hunter2" not in r.data
