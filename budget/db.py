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
    sort        INTEGER NOT NULL DEFAULT 0,
    bank_from   TEXT                               -- YYYY-MM: month of the last balance checked against the bank;
                                                   -- bank exports are the record from its 1st, older months stay as they were
);

CREATE TABLE IF NOT EXISTS categories (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    kind          TEXT NOT NULL CHECK (kind IN ('income', 'expense', 'transfer')),
    sort          INTEGER NOT NULL DEFAULT 0,
    snap_to_month INTEGER NOT NULL DEFAULT 0,  -- 1 = count on the nearest 1st (rent paid a day early)
    grp           TEXT                         -- spending: fixed | flexible | nonmonthly; transfers: saving
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
    source      TEXT NOT NULL DEFAULT 'user',  -- user | config | excel | builtin
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    amount      REAL                           -- only match this exact amount; NULL matches any
);
-- migrate() adds the unique index: one rule per text, plus one per text-and-amount.

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
    date            TEXT NOT NULL,             -- YYYY-MM-DD, as the bank posted it
    date_override   TEXT,                      -- date you set by hand, if any
    effective_date  TEXT NOT NULL DEFAULT '',  -- date reports use: override, else nearest 1st for
                                               -- snap_to_month categories, else the bank date
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
    one_off         INTEGER NOT NULL DEFAULT 0, -- 1 = a big one-time purchase, kept out of its category's group
    dedupe_key      TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS ix_transactions_category ON transactions(category_id);
CREATE INDEX IF NOT EXISTS ix_transactions_account ON transactions(account_id);

CREATE TABLE IF NOT EXISTS balances (
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    month      TEXT NOT NULL,                  -- YYYY-MM
    amount     REAL NOT NULL,                  -- money in the account; for cards and loans, what's owed
    as_of      TEXT,                           -- YYYY-MM-DD the balance was true on (month end if unknown)
    source     TEXT NOT NULL DEFAULT 'manual', -- manual | excel (imports never overwrite manual)
    PRIMARY KEY (account_id, month)
);

-- Spreadsheet rows a bank export replaced (and the bank row that took their place), so re-importing
-- the spreadsheet leaves them out and re-uploading the export doesn't pair a bank row twice.
CREATE TABLE IF NOT EXISTS replaced_rows (
    dedupe_key  TEXT PRIMARY KEY,
    replaced_by TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- What each paycheck puts into a retirement account before it reaches the bank. An entry applies to
-- paychecks from start_date until the next entry for the same pattern (a raise, a new rate).
CREATE TABLE IF NOT EXISTS paycheck_savings (
    id            INTEGER PRIMARY KEY,
    pattern       TEXT NOT NULL,                -- text in the paycheck's bank description
    account_id    INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    start_date    TEXT NOT NULL,                -- YYYY-MM-DD
    base_pay      REAL NOT NULL,                -- pay per paycheck the percentages apply to
    employee_pct  REAL NOT NULL,                -- your contribution, % of base pay
    match_rate    REAL NOT NULL DEFAULT 0,      -- employer adds this % of your contribution...
    match_cap_pct REAL NOT NULL DEFAULT 0       -- ...on contributions up to this % of base pay
);

-- Changes the plan expects, per month: negative = less money (lost income, a new cost).
CREATE TABLE IF NOT EXISTS plan_items (
    id          INTEGER PRIMARY KEY,
    label       TEXT NOT NULL,
    amount      REAL NOT NULL,
    start_month TEXT NOT NULL,                  -- YYYY-MM
    end_month   TEXT                            -- YYYY-MM, inclusive; NULL = ongoing
);

-- Fixed-term loans. From counts_from on, an account's balance is calculated from its loans
-- instead of entered: the same math as =-FV(rate/12, DATEDIF(start, date, "m"), PMT(...), amount).
CREATE TABLE IF NOT EXISTS loan_terms (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    label       TEXT,
    principal   REAL NOT NULL,                  -- amount borrowed
    annual_rate REAL NOT NULL,                  -- percent, e.g. 6.5
    term_months INTEGER NOT NULL,
    start_date  TEXT NOT NULL,                  -- YYYY-MM-DD; payments counted in whole months since
    counts_from TEXT                            -- YYYY-MM-DD the calculation takes over (default start_date)
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
    for column, ddl in (("institution", "TEXT"), ("opened", "TEXT"), ("closed", "TEXT"),
                        ("sort", "INTEGER NOT NULL DEFAULT 0"), ("bank_from", "TEXT")):
        if column not in existing:
            conn.execute(f"ALTER TABLE accounts ADD COLUMN {column} {ddl}")

    existing = _columns(conn, "transactions")
    for column, ddl in (("date_override", "TEXT"), ("effective_date", "TEXT NOT NULL DEFAULT ''")):
        if column not in existing:
            conn.execute(f"ALTER TABLE transactions ADD COLUMN {column} {ddl}")

    if "snap_to_month" not in _columns(conn, "categories"):
        conn.execute("ALTER TABLE categories ADD COLUMN snap_to_month INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            f"UPDATE categories SET snap_to_month = 1 WHERE name IN ({', '.join('?' * len(seed.SNAP_TO_MONTH))})",
            tuple(seed.SNAP_TO_MONTH),
        )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_transactions_effective_date ON transactions(effective_date)")

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

    if "amount" not in _columns(conn, "rules"):
        # The old table had UNIQUE(match_on, pattern); rebuild it so an amount-specific rule can
        # sit alongside the general rule for the same text.
        conn.executescript("""
            CREATE TABLE rules_new (
                id          INTEGER PRIMARY KEY,
                match_on    TEXT NOT NULL DEFAULT 'raw' CHECK (match_on IN ('raw', 'name')),
                pattern     TEXT NOT NULL,
                rename_to   TEXT,
                category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                source      TEXT NOT NULL DEFAULT 'user',
                created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                amount      REAL
            );
            INSERT INTO rules_new (id, match_on, pattern, rename_to, category_id, source, created_at)
                SELECT id, match_on, pattern, rename_to, category_id, source, created_at FROM rules;
            DROP TABLE rules;
            ALTER TABLE rules_new RENAME TO rules;
        """)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_rules_match ON rules(match_on, pattern, COALESCE(amount, -1e18))")

    if "as_of" not in _columns(conn, "balances"):
        conn.execute("ALTER TABLE balances ADD COLUMN as_of TEXT")
    conn.execute("UPDATE balances SET as_of = date(month || '-01', '+1 month', '-1 day') WHERE as_of IS NULL")

    if "grp" not in _columns(conn, "categories"):
        conn.execute("ALTER TABLE categories ADD COLUMN grp TEXT")
    if "one_off" not in _columns(conn, "transactions"):
        conn.execute("ALTER TABLE transactions ADD COLUMN one_off INTEGER NOT NULL DEFAULT 0")

    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 1:
        _fold_old_categories(conn)
    if version < 2:
        for row in conn.execute("SELECT id, name, kind FROM categories WHERE grp IS NULL").fetchall():
            conn.execute("UPDATE categories SET grp = ? WHERE id = ?", (seed.category_group(row["name"], row["kind"]), row["id"]))
    conn.execute("PRAGMA user_version = 2")


def merge_category(conn, source_id, target_id):
    """Move a category's transactions and dictionary entries to another category, then delete it."""
    conn.execute("UPDATE transactions SET category_id = ? WHERE category_id = ?", (target_id, source_id))
    conn.execute("UPDATE rules SET category_id = ? WHERE category_id = ?", (target_id, source_id))
    conn.execute("DELETE FROM categories WHERE id = ?", (source_id,))


def _fold_old_categories(conn):
    """Version 1 folded 40 narrow categories into broader ones (seed.MERGED)."""
    from . import seed

    kinds = dict(seed.CATEGORIES)
    ids = {r["name"]: r["id"] for r in conn.execute("SELECT id, name FROM categories")}
    for old, new in seed.MERGED.items():
        if old not in ids:
            continue
        if new not in ids:
            ids[new] = conn.execute(
                "INSERT INTO categories (name, kind, sort, snap_to_month) VALUES (?, ?, 999, ?)",
                (new, kinds.get(new, "expense"), int(new in seed.SNAP_TO_MONTH)),
            ).lastrowid
        merge_category(conn, ids.pop(old), ids[new])
    order = {name: i for i, (name, _) in enumerate(seed.CATEGORIES)}
    for name, sort in order.items():
        conn.execute("UPDATE categories SET sort = ? WHERE name = ?", (sort, name))


def init_db(conn, personal=None):
    """Create tables, upgrade older databases, and load built-in and personal categories/rules."""
    from . import seed
    from .rules import reapply, sync_dates

    conn.executescript(SCHEMA)
    migrate(conn)
    if seed.seed_defaults(conn, personal):
        reapply(conn)  # personal.toml changed: bring existing transactions in line with it
    sync_dates(conn)
    conn.commit()
