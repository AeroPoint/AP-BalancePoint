"""SQLite connection handling, schema, and upgrades of older databases."""
import sqlite3

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL DEFAULT 'checking',  -- see seed.ACCOUNT_KINDS
    institution TEXT,
    opened      TEXT,                              -- YYYY-MM, optional
    closed      TEXT,                              -- YYYY-MM once the account is closed
    sort        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS categories (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE,
    kind  TEXT NOT NULL CHECK (kind IN ('income', 'expense', 'transfer')),
    sort  INTEGER NOT NULL DEFAULT 0
);

-- The merchant dictionary + auto-categorization rules.
--   match_on='raw'  : pattern is searched for in the bank's raw description
--   match_on='name' : pattern is searched for in the cleaned merchant name (e.g. "Gas")
CREATE TABLE IF NOT EXISTS rules (
    id          INTEGER PRIMARY KEY,
    match_on    TEXT NOT NULL DEFAULT 'raw' CHECK (match_on IN ('raw', 'name')),
    pattern     TEXT NOT NULL,
    rename_to   TEXT,
    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    source      TEXT NOT NULL DEFAULT 'user',  -- user | excel | builtin
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (match_on, pattern)
);

CREATE TABLE IF NOT EXISTS imports (
    id          INTEGER PRIMARY KEY,
    filename    TEXT NOT NULL,
    account_id  INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    kind        TEXT NOT NULL DEFAULT 'csv',   -- csv | excel
    uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    rows_read   INTEGER NOT NULL DEFAULT 0,
    rows_added  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY,
    account_id      INTEGER NOT NULL REFERENCES accounts(id),
    import_id       INTEGER REFERENCES imports(id) ON DELETE CASCADE,
    date            TEXT NOT NULL,             -- YYYY-MM-DD
    amount          REAL NOT NULL,             -- negative = money out
    raw_description TEXT NOT NULL,
    memo            TEXT,
    mcc             TEXT,                      -- merchant category code from US Bank memo
    name            TEXT NOT NULL,
    name_locked     INTEGER NOT NULL DEFAULT 0, -- 1 = typed by hand, rules won't rename
    category_id     INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    -- manual = picked by hand, sheet = hand-sorted in the old spreadsheet (rules leave both alone),
    -- rule = dictionary/keyword, mcc = guessed from card type, none = uncategorized
    category_source TEXT NOT NULL DEFAULT 'none',
    notes           TEXT,
    dedupe_key      TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS ix_transactions_category ON transactions(category_id);
CREATE INDEX IF NOT EXISTS ix_transactions_account ON transactions(account_id);

CREATE TABLE IF NOT EXISTS balances (
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    month      TEXT NOT NULL,                  -- YYYY-MM
    amount     REAL NOT NULL,                  -- money in the account; for cards and loans, what's owed
    source     TEXT NOT NULL DEFAULT 'manual', -- manual | excel (imports never overwrite manual)
    PRIMARY KEY (account_id, month)
);
"""


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def _columns(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn):
    """Bring databases created by earlier versions up to the current schema."""
    from . import seed

    existing = _columns(conn, "accounts")
    for column, ddl in (("institution", "TEXT"), ("opened", "TEXT"), ("closed", "TEXT"), ("sort", "INTEGER NOT NULL DEFAULT 0")):
        if column not in existing:
            conn.execute(f"ALTER TABLE accounts ADD COLUMN {column} {ddl}")

    # Net worth used to live in separate nw_items / nw_values tables; fold them into accounts.
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'nw_items'").fetchone():
        items = conn.execute(
            "SELECT i.*, (SELECT MAX(month) FROM nw_values v WHERE v.item_id = i.id) AS last_month FROM nw_items i"
        ).fetchall()
        for item in items:
            if item["last_month"] is None:
                continue  # never used
            conn.execute(
                "INSERT OR IGNORE INTO accounts (name, kind) VALUES (?, ?)",
                (item["name"], seed.guess_kind(item["name"], item["kind"])),
            )
            account_id = conn.execute("SELECT id FROM accounts WHERE name = ?", (item["name"],)).fetchone()["id"]
            if not item["active"]:
                conn.execute("UPDATE accounts SET closed = COALESCE(closed, ?) WHERE id = ?", (item["last_month"], account_id))
            conn.execute(
                "INSERT OR IGNORE INTO balances (account_id, month, amount, source) "
                "SELECT ?, month, amount, 'manual' FROM nw_values WHERE item_id = ?",
                (account_id, item["id"]),
            )
        conn.execute("DROP TABLE nw_values")
        conn.execute("DROP TABLE nw_items")


def init_db(conn, personal=None):
    """Create tables, upgrade older databases, and load built-in and personal categories/rules."""
    from . import seed
    from .rules import reapply

    conn.executescript(SCHEMA)
    migrate(conn)
    if seed.seed_defaults(conn, personal):
        reapply(conn)  # personal.toml changed: bring existing transactions in line with it
    conn.commit()
