"""Pull balances and transactions from SimpleFIN Bridge (bridge.simplefin.org): a cheap ($1.50/mo,
up to 25 institutions), read-only bank-data aggregator. Protocol: https://www.simplefin.org/protocol.html

One-time setup: run.py simplefin-setup claims a setup token, then simplefin-map ties each SimpleFIN
account to a local one. After that, run.py simplefin-sync (daily, from sync-mac.sh) stores transactions
through the same de-duplicated path a CSV upload uses (csv_import.store_transactions).

Each mapped account keeps its own last_synced date, which only moves when its bank came back without an
error. A run pulls from the oldest of those (less a few days of overlap), so a missed day, a computer
that was off for a week, or a bank that needed signing in again at the Bridge all catch up on their own.
"""
import base64
import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import seed
from .csv_import import replace_spreadsheet_rows, store_transactions
from .rules import RuleEngine, normalize, sync_dates

ACCESS_URL_FILE = "simplefin-access-url"  # in the data folder; a credential, so kept out of budget.db
MAX_WINDOW_DAYS = 45                      # the Bridge's recommended longest span per /accounts call
OVERLAP_DAYS = 5                          # re-read the last few days every run, for late postings
STALE_DAYS = 2                            # warn when an account's last good pull is older than this
TRANSACTION_KINDS = ("checking", "savings", "credit", "cash")  # others sync their balance only
PENDING_MATCH_DAYS = 10    # a pending charge's posted version shows up within this many days
PENDING_KEEP_DAYS = 14     # a pending charge that never posts (a released hold) is dropped after this
# The Bridge sits behind Cloudflare, which turns away Python's default "Python-urllib" client
# ("error code: 1010") before the request ever reaches SimpleFIN.
USER_AGENT = "ledger-budget-app/1.0 (+https://www.simplefin.org/protocol.html)"


class SimpleFinError(RuntimeError):
    pass


# ---------------------------------------------------------------- the access URL (a credential)

def access_url_path(data_dir):
    return Path(data_dir) / ACCESS_URL_FILE


def save_access_url(data_dir, access_url):
    """Write the access URL readable only by this user, and keep it out of data/'s own git repo."""
    path = access_url_path(data_dir)
    path.write_text(access_url + "\n", encoding="utf-8")
    path.chmod(0o600)
    ignore = Path(data_dir) / ".gitignore"
    lines = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
    if ACCESS_URL_FILE not in lines:
        ignore.write_text("\n".join(lines + [ACCESS_URL_FILE]) + "\n", encoding="utf-8")


def load_access_url(data_dir):
    path = access_url_path(data_dir)
    return path.read_text(encoding="utf-8").strip() if path.exists() else None


# ---------------------------------------------------------------- talking to the Bridge

def claim_setup_token(setup_token):
    """One-time: exchange a Setup Token for a long-lived Access URL. The token is single-use."""
    try:
        claim_url = base64.b64decode("".join(setup_token.split()), validate=True).decode()  # line breaks from pasting
    except (ValueError, UnicodeDecodeError) as exc:
        raise SimpleFinError(f"That doesn't look like a SimpleFIN setup token: {exc}") from exc
    if urllib.parse.urlsplit(claim_url).scheme not in ("http", "https") or "/claim/" not in claim_url:
        raise SimpleFinError("That doesn't look like a SimpleFIN setup token (it should decode to a claim link).")
    req = urllib.request.Request(claim_url, method="POST", headers={"Content-Length": "0", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            access_url = resp.read().decode().strip()
    except urllib.error.HTTPError as exc:
        said = _server_said(exc)
        if said.startswith("error code:"):  # Cloudflare, not SimpleFIN: the token wasn't used
            raise SimpleFinError(f"Cloudflare blocked the request before it reached SimpleFIN ({said}); "
                                 "the token wasn't used, so try again.") from exc
        raise SimpleFinError(f"SimpleFIN Bridge turned down the setup token (HTTP {exc.code}: {said}). "
                             "A token works once; if it was claimed, make a new one at bridge.simplefin.org.") from exc
    except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        raise SimpleFinError(f"Couldn't reach SimpleFIN Bridge: {getattr(exc, 'reason', exc)!s}") from exc
    if not access_url.startswith("http"):
        raise SimpleFinError(f"Unexpected response claiming the setup token: {access_url[:80]!r}")
    return access_url


def _server_said(exc):
    """The first line of an error response's body, for the message."""
    try:
        text = exc.read(300).decode(errors="replace").strip()
    except OSError:
        return "no details"
    return (text.splitlines() or ["no details"])[0][:150]


def _get_json(access_url, path, params):
    parts = urllib.parse.urlsplit(access_url)
    auth = base64.b64encode(
        f"{urllib.parse.unquote(parts.username or '')}:{urllib.parse.unquote(parts.password or '')}".encode()
    ).decode()
    netloc = parts.hostname + (f":{parts.port}" if parts.port else "")
    url = urllib.parse.urlunsplit(
        (parts.scheme, netloc, parts.path.rstrip("/") + path, urllib.parse.urlencode(params), "")
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        said = _server_said(exc)
        hint = (" The access may have been revoked; run simplefin-setup with a new token."
                if exc.code in (401, 403) and not said.startswith("error code:") else "")
        raise SimpleFinError(f"SimpleFIN Bridge returned HTTP {exc.code} for {path} ({said}).{hint}") from exc
    except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        raise SimpleFinError(f"Couldn't reach SimpleFIN Bridge: {getattr(exc, 'reason', exc)!s}") from exc
    except json.JSONDecodeError as exc:
        raise SimpleFinError(f"SimpleFIN Bridge sent something that isn't JSON for {path}") from exc


def _epoch(day):
    return int(datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc).timestamp())


def _day(epoch):
    return datetime.fromtimestamp(epoch, tz=timezone.utc).date()


# Some banks put the transaction's date in the description at first ("Electronic Deposit 03/14 Apple Cash")
# and drop it later, and payees come out as "Zelle To 03/15 Pat Smith". The date is left out, so the same
# charge keeps one description (no duplicate when the text changes) and rules match every month.
DATE_IN_TEXT = re.compile(r"(?<!\S)\d{1,2}/\d{1,2}(?:/\d{2,4})?(?!\S)")


def clean_description(text):
    return " ".join(DATE_IN_TEXT.sub(" ", text or "").split())


def _org_name(account):
    org = account.get("org")
    return org.get("name") if isinstance(org, dict) else org


def fetch_accounts(access_url, start, end):
    """/accounts for [start, end) (dates), in as many calls as SimpleFIN's 90-day cap needs.

    Returns (accounts, errors). An account's transactions are merged across calls; its balance is the
    current one whatever the window, so the last call's is kept. Version 2 names each account's bank in
    a separate "connections" list; it's copied onto the account as "org".
    """
    accounts, errors = {}, []
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=MAX_WINDOW_DAYS), end)
        data = _get_json(access_url, "/accounts", {
            "version": "2", "pending": "1", "start-date": _epoch(chunk_start), "end-date": _epoch(chunk_end),
        })
        banks = {c.get("conn_id"): c.get("org_name") or c.get("name") for c in data.get("connections") or []}
        for a in data.get("accounts", []):
            merged = accounts.setdefault(a["id"], {**a, "transactions": []})
            if not merged.get("org") and banks.get(a.get("conn_id")):
                merged["org"] = banks[a["conn_id"]]
            merged["balance"] = a.get("balance", merged.get("balance"))
            merged["balance-date"] = a.get("balance-date", merged.get("balance-date"))
            merged["transactions"].extend(a.get("transactions") or [])
        errors.extend(data.get("errlist") or data.get("errors") or [])
        chunk_start = chunk_end
    return list(accounts.values()), errors


def list_accounts(access_url):
    """Every account the Bridge knows about: [(id, org, label, balance)], errors."""
    today = datetime.now(timezone.utc).date()
    fetched, errors = fetch_accounts(access_url, today - timedelta(days=1), today + timedelta(days=1))
    return [(a["id"], _org_name(a), a["name"], a.get("balance")) for a in fetched], errors


def to_rows(transactions, sync_from=None):
    """SimpleFIN transactions -> the {date, amount, raw, memo, mcc} rows parse_csv() makes.

    Pending ones are left for the day they post, so they're never counted twice.
    """
    out = []
    for t in transactions:
        if t.get("pending") or not t.get("posted"):
            continue
        day = _day(t["posted"]).isoformat()
        if sync_from and day < sync_from:
            continue
        out.append({"date": day, "amount": round(float(t["amount"]), 2),
                    "raw": clean_description(t.get("description")), "memo": None, "mcc": None})
    return out


def set_synced_balance(conn, account_id, amount, as_of):
    """Upsert the month's balance from the bank. Unlike other imports, it also replaces a balance typed
    in by hand, but only an older one: the bank's newer number is the better one, while a number you
    type with a later date still wins."""
    conn.execute(
        """INSERT INTO balances (account_id, month, amount, as_of, source) VALUES (?, ?, ?, ?, 'simplefin')
           ON CONFLICT (account_id, month) DO UPDATE
             SET amount = excluded.amount, as_of = excluded.as_of, source = 'simplefin'
           WHERE balances.source != 'manual' OR COALESCE(balances.as_of, '') < excluded.as_of""",
        (account_id, as_of[:7], amount, as_of),
    )


def store_pending(conn, account_id, transactions, sync_from, today):
    """Charges still pending that happened in a finished month count in that month: each is kept as a
    pending row (one per SimpleFIN id) until its posted version arrives (settle_pending). Pending charges
    from the current month are left to post normally. Returns how many were added."""
    month_start = today.replace(day=1).isoformat()
    engine = RuleEngine(conn)
    added = 0
    for t in transactions:
        if not t.get("pending") or not t.get("transacted_at"):
            continue
        day = _day(t["transacted_at"]).isoformat()
        if day >= month_start or (sync_from and day < sync_from) or (today - _day(t["transacted_at"])).days > PENDING_KEEP_DAYS:
            continue
        amount, raw = round(float(t["amount"]), 2), clean_description(t.get("description"))
        m = engine.resolve(raw, None, None, amount, account_id)
        added += conn.execute(
            """INSERT OR IGNORE INTO transactions (account_id, date, effective_date, amount, raw_description, name,
                   category_id, category_source, dedupe_key, pending) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (account_id, day, day, amount, raw, m.name, m.category_id, m.source, f"sfpending|{account_id}|{t['id']}"),
        ).rowcount
    return added


PENDING_NOW_KEY = "pending_this_month"


def pending_this_month(conn, account_id, transactions, sync_from, today):
    """This month's charges that are still pending, as {account_id, amount, raw, category_id} dicts. They
    aren't stored as transactions (they post within days); the pace check counts them so it isn't
    behind early in the month. Replaced by every sync."""
    month_start = today.replace(day=1).isoformat()
    engine = RuleEngine(conn)
    out = []
    for t in transactions:
        if not t.get("pending") or not t.get("transacted_at"):
            continue
        day = _day(t["transacted_at"]).isoformat()
        if day < month_start or (sync_from and day < sync_from):
            continue
        amount, raw = round(float(t["amount"]), 2), clean_description(t.get("description"))
        out.append({"account_id": account_id, "amount": amount, "raw": raw,
                    "category_id": engine.resolve(raw, None, None, amount, account_id).category_id})
    return out


def settle_pending(conn, account_id, today):
    """A pending row gives way to its posted charge, which keeps counting in the pending row's month and
    takes anything decided on it (a category picked by hand, a checkbox, notes, one-off). The posted
    charge is the same amount, or the same merchant within 30% (a tip added). A pending row that never
    posts goes after PENDING_KEEP_DAYS. Returns (settled, dropped)."""
    settled = dropped = 0
    used = set()
    for p in conn.execute("SELECT * FROM transactions WHERE account_id = ? AND pending = 1 ORDER BY date, id", (account_id,)).fetchall():
        until = (date.fromisoformat(p["date"]) + timedelta(days=PENDING_MATCH_DAYS)).isoformat()
        # A split charge counts as the whole charge (its bank row at the bank's amount), never part by part.
        candidates = [{**dict(c), "amount": c["amount"] if c["split_total"] is None else c["split_total"]} for c in conn.execute(
            """SELECT * FROM transactions WHERE account_id = ? AND pending = 0 AND date >= ? AND date <= ?
               AND date_override IS NULL AND split_of IS NULL ORDER BY date, id""", (account_id, p["date"], until))
            if c["id"] not in used]
        key = normalize(p["raw_description"])[:10]
        match = next((c for c in candidates if round(c["amount"], 2) == round(p["amount"], 2)), None) or next(
            (c for c in candidates if normalize(c["raw_description"])[:10] == key
             and abs(c["amount"] - p["amount"]) <= 0.3 * abs(p["amount"])), None)
        if match:
            used.add(match["id"])
            updates = {}
            if match["date"][:7] != p["date"][:7]:
                updates["date_override"] = p["date"]
                updates["notes"] = match["notes"] or f"Pending on {p['date'][:7]} month end: counts in that month"
            if p["category_source"] in ("manual", "sheet"):
                updates.update(category_id=p["category_id"], category_source=p["category_source"])
            for col in ("flag", "one_off"):
                if p[col] and not match[col]:
                    updates[col] = p[col]
            if p["notes"] and not match["notes"]:
                updates["notes"] = p["notes"]
            where = "id = ?"
            if match["split_total"] is not None:
                # Already split by hand: its parts keep their categories and move to the month together.
                updates = {k: v for k, v in updates.items() if k in ("date_override", "notes")}
                where = "id = ? OR split_of = ?"
            if updates:
                conn.execute(f"UPDATE transactions SET {', '.join(f'{k} = ?' for k in updates)} WHERE {where}",
                             (*updates.values(), *[match["id"]] * where.count("?")))
            conn.execute("DELETE FROM transactions WHERE id = ?", (p["id"],))
            settled += 1
        elif (today - date.fromisoformat(p["date"])).days > PENDING_KEEP_DAYS:
            conn.execute("DELETE FROM transactions WHERE id = ?", (p["id"],))
            dropped += 1
    if settled or dropped:
        sync_dates(conn, "account_id = ?", (account_id,))
    return settled, dropped


def count_in_month_happened(conn, account_id, transactions, sync_from):
    """A posted charge that happened in an earlier month than it posted (bought on the 29th, posted on the
    1st) counts in the month it happened, whether or not a sync saw it pending. Not for categories that
    count on the nearest 1st (rent, mortgage), and never over a date set by hand. Returns how many."""
    moved = 0
    for t in transactions:
        if t.get("pending") or not t.get("posted") or not t.get("transacted_at"):
            continue
        posted, happened = _day(t["posted"]), _day(t["transacted_at"])
        if happened.strftime("%Y-%m") >= posted.strftime("%Y-%m") or (posted - happened).days > PENDING_MATCH_DAYS:
            continue
        if sync_from and posted.isoformat() < sync_from:
            continue
        # A split charge is found by its bank row (the bank's amount is in split_total), and its parts move with it.
        ids = [r[0] for r in conn.execute(
            """SELECT id FROM transactions
               WHERE account_id = ? AND date = ? AND ROUND(COALESCE(split_total, amount), 2) = ? AND raw_description = ?
                 AND pending = 0 AND date_override IS NULL AND split_of IS NULL
                 AND (category_id IS NULL OR category_id NOT IN (SELECT id FROM categories WHERE snap_to_month = 1))""",
            (account_id, posted.isoformat(), round(float(t["amount"]), 2), clean_description(t.get("description"))),
        )]
        if not ids:
            continue
        marks = ", ".join("?" * len(ids))
        conn.execute(
            f"""UPDATE transactions SET date_override = ?, notes = COALESCE(notes, ?)
                WHERE (id IN ({marks}) OR split_of IN ({marks})) AND date_override IS NULL""",
            (happened.isoformat(), f"Happened {happened.isoformat()}, posted {posted.isoformat()}: counts in {happened.strftime('%Y-%m')}",
             *ids, *ids),
        )
        moved += len(ids)
    if moved:
        sync_dates(conn, "account_id = ?", (account_id,))
    return moved


def _failed(errors):
    """(connection ids, account ids) the Bridge reported a problem with; None = can't tell which.

    errlist codes are gen.* (notices about the request, like a long date range), con.* (a bank
    connection, e.g. con.auth: sign in again) or act.* (one account). Only the last two mean data may be
    missing; one that doesn't say which connection or account it's about counts against all of them.
    """
    conns, accounts = set(), set()
    for e in errors:
        if isinstance(e, dict) and str(e.get("code", "")).startswith("gen."):
            continue
        if not isinstance(e, dict) or not (e.get("conn_id") or e.get("account_id")):
            return None
        conns.add(e.get("conn_id"))
        accounts.add(e.get("account_id"))
    return conns - {None}, accounts - {None}


# ---------------------------------------------------------------- mapping and syncing

def map_account(conn, external_id, account_id, transactions=None, sync_from=None):
    """Tie a SimpleFIN account to a local one. Returns (transactions?, sync_from).

    transactions: None = by the account's kind (TRANSACTION_KINDS). sync_from: None = the day after the
    account's latest transaction, so the sync picks up exactly where CSV uploads left off.
    """
    kind = conn.execute("SELECT kind FROM accounts WHERE id = ?", (account_id,)).fetchone()["kind"]
    if transactions is None:
        transactions = kind in TRANSACTION_KINDS
    if sync_from is None:
        latest = conn.execute("SELECT MAX(date) FROM transactions WHERE account_id = ?", (account_id,)).fetchone()[0]
        sync_from = (date.fromisoformat(latest) + timedelta(days=1)).isoformat() if latest else date.today().isoformat()
    conn.execute(
        """INSERT INTO simplefin_accounts (external_id, account_id, transactions, sync_from) VALUES (?, ?, ?, ?)
           ON CONFLICT (external_id) DO UPDATE SET account_id = excluded.account_id,
             transactions = excluded.transactions, sync_from = excluded.sync_from, last_synced = NULL""",
        (external_id, account_id, int(transactions), sync_from),
    )
    return transactions, sync_from


def status(conn, today=None):
    """[dict] for each mapped account: names, mode, sync_from, last_synced, stale?"""
    today = today or date.today()
    out = []
    for r in conn.execute(
        "SELECT s.*, a.name AS account FROM simplefin_accounts s JOIN accounts a ON a.id = s.account_id ORDER BY a.sort, a.name"
    ):
        row = dict(r)
        row["stale"] = not row["last_synced"] or (today - date.fromisoformat(row["last_synced"])).days > STALE_DAYS
        out.append(row)
    return out


def sync(conn, access_url, today=None, since=None):
    """Pull every mapped account from its last good sync (or since, if given) through today.

    Returns {"start", "end", "accounts": [dict], "unmapped": [(id, org, label)], "missing": [local
    names], "errors": [...], "stale": [dict]}. Raises SimpleFinError if the Bridge couldn't be reached,
    in which case nothing was stored and no watermark moved. The caller commits.
    """
    today = today or date.today()
    mapped = {r["external_id"]: dict(r) for r in conn.execute(
        "SELECT s.*, a.name AS account, a.kind FROM simplefin_accounts s JOIN accounts a ON a.id = s.account_id"
    )}
    if since:
        start = since
    elif mapped:
        start = min(date.fromisoformat(m["last_synced"] or m["sync_from"]) for m in mapped.values())
    else:
        start = today
    start -= timedelta(days=OVERLAP_DAYS)
    end = today + timedelta(days=1)  # end-date is exclusive; include what posted today
    fetched, errors = fetch_accounts(access_url, start, end)
    failed = _failed(errors)

    results, unmapped, seen = [], [], set()
    balances = {}  # local account id -> [total, latest as_of]: several bank accounts can feed one
    pending_now = []
    for a in fetched:
        m = mapped.get(a["id"])
        if m is None:
            unmapped.append((a["id"], _org_name(a), a["name"]))
            continue
        seen.add(a["id"])
        read = added = pending = 0
        settled = dropped = 0
        if m["transactions"]:
            rows = to_rows(a.get("transactions") or [], m["sync_from"])
            if rows:
                _, read, added, _, _ = store_transactions(conn, m["account_id"], "SimpleFIN sync", rows, kind="csv")
                replace_spreadsheet_rows(conn, m["account_id"], rows, "SimpleFIN sync")
            settled, dropped = settle_pending(conn, m["account_id"], today)
            count_in_month_happened(conn, m["account_id"], a.get("transactions") or [], m["sync_from"])
            pending = store_pending(conn, m["account_id"], a.get("transactions") or [], m["sync_from"], today)
            pending_now += pending_this_month(conn, m["account_id"], a.get("transactions") or [], m["sync_from"], today)
        balance = None
        if a.get("balance") not in (None, ""):
            balance = float(a["balance"])
            # A balance is a moment, so it takes this Mac's local date (a UTC date can be tomorrow here in
            # the evening, leaving no balance on or before today), and never one later than today.
            as_of = min(datetime.fromtimestamp(a["balance-date"]).date() if a.get("balance-date") else today, today).isoformat()
            total = balances.setdefault(m["account_id"], [0.0, as_of, m["kind"]])
            total[0] += balance
            total[1] = max(total[1], as_of)
        ok = failed is not None and a.get("conn_id") not in failed[0] and a["id"] not in failed[1]
        conn.execute(
            "UPDATE simplefin_accounts SET org = ?, label = ?, last_synced = COALESCE(?, last_synced) WHERE external_id = ?",
            (_org_name(a), a["name"], today.isoformat() if ok else None, a["id"]),
        )
        if balance is not None and seed.account_side(m["kind"]) == "liability":
            balance = abs(balance)  # shown the way it's stored: what's owed
        results.append({"org": _org_name(a), "label": a["name"], "account": m["account"], "added": added,
                        "read": read, "balance": balance, "ok": ok, "pending": pending, "settled": settled,
                        "dropped": dropped})
    missing = [m["account"] for ext, m in mapped.items() if ext not in seen]
    for account_id, (total, as_of, kind) in balances.items():
        if any(m["account_id"] == account_id and ext not in seen for ext, m in mapped.items()):
            continue  # part of this account didn't come back; a partial sum would be wrong
        if seed.account_side(kind) == "liability":
            total = abs(total)  # stored as what's owed
        set_synced_balance(conn, account_id, round(total, 2), as_of)
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (PENDING_NOW_KEY, json.dumps({"month": today.isoformat()[:7], "items": pending_now})))
    stale = [s for s in status(conn, today) if s["stale"]]
    return {"start": start, "end": today, "accounts": results, "unmapped": unmapped, "missing": missing,
            "errors": errors, "stale": stale}
