"""Manage data: the app's bulk erases in one place, each saving a restore point first.

Every erase on the Manage data page first copies the whole database into data/restore-points/ (SQLite's
own backup, safe while the app runs), and the page can put any of those copies back. The newest
KEEP_RESTORE_POINTS are kept. Restore points stay on this computer: they're left out of data/'s own git
repo (that repo's history already holds every version the sync committed).

The erases:
  * amazon      every stored Amazon item; transactions and their categories stay
  * import      one import and the transactions it added
  * everything  every table emptied, then the built-in categories and dictionary loaded again, like a new
                install. Files in the data folder (uploads, workbooks, personal.toml, the bank and email
                credentials, restore points) stay. ERASED_KEY records the day, so the backup check knows it
                was meant.

backup_check() is for the scheduled sync (sync-mac.sh), before it commits the database to data/'s git repo:
a database that suddenly lost most of its transactions, with no erase on this page in the last day or so,
is a mistake or damage, so the commit waits and the log says so. git history, the nightly backup and the
PC's copy never replace older versions anyway; this keeps a bad copy from becoming the newest one.
"""
import re
import sqlite3
import subprocess
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

RESTORE_DIR = "restore-points"
KEEP_RESTORE_POINTS = 10
ERASED_KEY = "data_erased_on"        # settings: the day Erase everything ran (YYYY-MM-DD)
ERASE_WORD = "ERASE"                 # typed to confirm Erase everything
DROP_SHARE = 0.5                     # backup_check: under half the last backup's transactions...
DROP_MIN = 50                        # ...when that backup had at least this many
ERASE_GRACE_DAYS = 2                 # an erase this recent explains a drop


def restore_dir(data_dir):
    return Path(data_dir) / RESTORE_DIR


def _ignore_in_git(data_dir):
    ignore = Path(data_dir) / ".gitignore"
    lines = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
    if RESTORE_DIR + "/" not in lines:
        ignore.write_text("\n".join(lines + [RESTORE_DIR + "/"]) + "\n", encoding="utf-8")


def make_restore_point(conn, data_dir, reason):
    """Copy the whole database to data/restore-points/<when>_<reason>.db; keep the newest few."""
    folder = restore_dir(data_dir)
    folder.mkdir(exist_ok=True)
    _ignore_in_git(data_dir)
    slug = re.sub(r"[^a-z0-9]+", "-", reason.lower()).strip("-")[:40] or "restore-point"
    path = folder / f"{datetime.now():%Y-%m-%d_%H%M%S}_{slug}.db"
    conn.commit()
    target = sqlite3.connect(path)
    try:
        conn.backup(target)
    finally:
        target.close()
    for old in restore_points(data_dir)[KEEP_RESTORE_POINTS:]:
        old["path"].unlink(missing_ok=True)
    return path


def restore_points(data_dir):
    """Newest first: [{name, path, when, reason, size}]."""
    folder = restore_dir(data_dir)
    out = []
    for path in sorted(folder.glob("*.db"), reverse=True) if folder.is_dir() else []:
        m = re.match(r"(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})(\d{2})_(.*)\.db$", path.name)
        if not m:
            continue
        out.append({"name": path.name, "path": path, "when": f"{m[1]} {m[2]}:{m[3]}",
                    "reason": m[5].replace("-", " "), "size": path.stat().st_size})
    return out


def restore(conn, data_dir, name, personal=None):
    """Put a restore point back in place of the whole database (saving the current one as a restore
    point first). Returns the restore point read, or None when there's no such file."""
    from .db import init_db

    point = next((p for p in restore_points(data_dir) if p["name"] == name), None)
    if point is None:
        return None
    make_restore_point(conn, data_dir, f"before restoring {point['when']}")
    source = sqlite3.connect(point["path"])
    try:
        source.backup(conn)
    finally:
        source.close()
    init_db(conn, personal)  # a restore point from an older version gets its tables brought up to date
    return point


def counts(conn):
    """What the page lists: how much of each kind of data there is."""
    q = lambda sql: conn.execute(sql).fetchone()[0]
    return {"transactions": q("SELECT COUNT(*) FROM transactions"), "accounts": q("SELECT COUNT(*) FROM accounts"),
            "imports": q("SELECT COUNT(*) FROM imports"), "amazon_items": q("SELECT COUNT(*) FROM amazon_items"),
            "amazon_charges": q("SELECT COUNT(DISTINCT transaction_id) FROM amazon_items"),
            "balances": q("SELECT COUNT(*) FROM balances")}


def erase_amazon(conn):
    return conn.execute("DELETE FROM amazon_items").rowcount


def erase_import(conn, import_id):
    """(file name, transactions removed) or None."""
    row = conn.execute("SELECT filename FROM imports WHERE id = ?", (import_id,)).fetchone()
    if row is None:
        return None
    removed = conn.execute("SELECT COUNT(*) FROM transactions WHERE import_id = ?", (import_id,)).fetchone()[0]
    conn.execute("DELETE FROM imports WHERE id = ?", (import_id,))
    return row["filename"], removed


def erase_everything(conn, personal=None, today=None):
    """Empty every table, then load the built-in categories and dictionary again, like a new install."""
    from .db import init_db

    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for table in tables:
            conn.execute(f'DELETE FROM "{table}"')
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn, personal)
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                 (ERASED_KEY, (today or date.today()).isoformat()))
    conn.commit()


# ---------------------------------------------------------------- the scheduled backup's check

def _transactions_in(path):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    except sqlite3.DatabaseError:
        return None
    finally:
        db.close()


def backup_check(data_dir, today=None):
    """(ok, message) for committing data/budget.db to data/'s git repo. Not ok when the database has
    under DROP_SHARE of the last commit's transactions (or can't be read), unless Erase everything ran
    in the last ERASE_GRACE_DAYS days."""
    data_dir = Path(data_dir)
    now = _transactions_in(data_dir / "budget.db")
    if now is None:
        return False, "the database can't be read (damaged?); not backed up over the last good copy"
    with tempfile.TemporaryDirectory() as tmp:
        last = Path(tmp) / "last.db"
        shown = subprocess.run(["git", "-C", str(data_dir), "show", "HEAD:budget.db"], capture_output=True)
        if shown.returncode != 0:
            return True, "no earlier backup to compare with"
        last.write_bytes(shown.stdout)
        before = _transactions_in(last)
    if before is None or before < DROP_MIN or now >= before * DROP_SHARE:
        return True, f"{now:,} transactions (last backup {before or 0:,})"
    db = sqlite3.connect(f"file:{data_dir / 'budget.db'}?mode=ro", uri=True)
    try:
        erased = db.execute("SELECT value FROM settings WHERE key = ?", (ERASED_KEY,)).fetchone()
    finally:
        db.close()
    today = today or date.today()
    if erased and erased[0] >= (today - timedelta(days=ERASE_GRACE_DAYS)).isoformat():
        return True, f"erased on purpose on {erased[0]} (Manage data): {now:,} transactions, last backup {before:,}"
    return False, (f"the database has {now:,} transactions, the last backup {before:,}, and nothing was erased on "
                   "Manage data: not backed up over it. If that's right, restore from Manage data or the "
                   "data/ git history; if it was meant, run the sync again with --force-backup.")
