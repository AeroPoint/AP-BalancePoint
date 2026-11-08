"""Manage data: restore points before every erase, putting one back, Erase everything, and the scheduled
backup's check against committing a database that lost most of its data (made-up data)."""
import subprocess
from datetime import date
from pathlib import Path

from budget import manage_data

from conftest import account, txn


def data_dir(app):
    return Path(app.config["DATABASE"]).parent


def test_the_page_lists_what_each_erase_does(client):
    html = client.get("/manage-data").get_data(as_text=True)
    for text in ("Restore points", "Amazon items", "Remove an import", "Erase everything", "Type ERASE to confirm",
                 "Stays:", "Erased:"):
        assert text in html
    assert 'href="/manage-data"' in client.get("/").get_data(as_text=True)  # in the menu


def test_erase_everything_needs_the_word_saves_a_restore_point_and_can_be_undone(app, conn, client):
    chk = account(conn, "Checking")
    txn(conn, chk, "2026-03-05", -40, "Groceries")
    conn.execute("INSERT INTO rules (match_on, pattern, rename_to, source) VALUES ('raw', 'CORNER SHOP', 'Corner Shop', 'user')")
    conn.commit()
    client.post("/manage-data/everything", data={"confirm": "erase"})
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1  # wrong word: nothing happens

    client.post("/manage-data/everything", data={"confirm": "ERASE"})
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM rules WHERE source = 'user'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM categories WHERE name = 'Groceries'").fetchone()[0] == 1  # built-ins back
    assert conn.execute("SELECT value FROM settings WHERE key = ?", (manage_data.ERASED_KEY,)).fetchone()[0] == date.today().isoformat()
    points = manage_data.restore_points(data_dir(app))
    assert [p["reason"] for p in points] == ["before erasing everything"]
    assert "restore-points/" in (data_dir(app) / ".gitignore").read_text()

    client.post("/manage-data/restore", data={"name": points[0]["name"]})
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM rules WHERE pattern = 'CORNER SHOP'").fetchone()[0] == 1
    assert [p["reason"] for p in manage_data.restore_points(data_dir(app))][0].startswith("before restoring")


def test_removing_an_import_saves_a_restore_point(app, conn, client):
    chk = account(conn, "Checking")
    iid = conn.execute("INSERT INTO imports (filename, kind, account_id, rows_read) VALUES ('jan.csv', 'csv', ?, 1)",
                       (chk,)).lastrowid
    txn(conn, chk, "2026-01-05", -10, "Groceries", import_id=iid)
    conn.commit()
    assert "jan.csv" in client.get("/manage-data").get_data(as_text=True)
    client.post(f"/imports/{iid}/delete")
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    assert manage_data.restore_points(data_dir(app))[0]["reason"] == "before removing import jan csv"


def test_only_the_newest_restore_points_are_kept(app, conn, monkeypatch):
    stamps = iter(f"2026-01-{d:02d}" for d in range(1, 20))

    class Clock:
        @staticmethod
        def now():
            from datetime import datetime
            return datetime.fromisoformat(next(stamps) + "T10:00:00")

    monkeypatch.setattr(manage_data, "datetime", Clock)
    for n in range(manage_data.KEEP_RESTORE_POINTS + 3):
        manage_data.make_restore_point(conn, data_dir(app), f"test {n}")
    points = manage_data.restore_points(data_dir(app))
    assert len(points) == manage_data.KEEP_RESTORE_POINTS and points[0]["reason"] == "test 12"


def _git(folder, *args):
    subprocess.run(["git", "-C", str(folder), *args], check=True, capture_output=True)


def _committed(folder):
    _git(folder, "add", "budget.db")
    _git(folder, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "backup")


def test_the_backup_holds_back_a_database_that_lost_most_of_its_data(app, conn):
    folder = data_dir(app)
    chk = account(conn, "Checking")
    for n in range(60):
        txn(conn, chk, f"2026-02-{n % 28 + 1:02d}", -1 - n, "Groceries")
    conn.commit()
    _git(folder, "init", "-q")
    _committed(folder)
    assert manage_data.backup_check(folder)[0]

    conn.execute("DELETE FROM transactions WHERE id > 10")  # not through Manage data: a mistake or damage
    conn.commit()
    ok, message = manage_data.backup_check(folder)
    assert not ok and "nothing was erased" in message

    manage_data.erase_everything(conn)  # on purpose: fine to back up
    ok, message = manage_data.backup_check(folder)
    assert ok and "erased on purpose" in message
    assert not manage_data.backup_check(folder, today=date.fromordinal(date.today().toordinal() + 5))[0]


def test_a_database_that_cant_be_read_is_held_back(tmp_path):
    (tmp_path / "budget.db").write_bytes(b"not a database at all" * 100)
    assert not manage_data.backup_check(tmp_path)[0]
