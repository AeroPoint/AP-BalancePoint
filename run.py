"""Start the budget app, or run a one-off import from the command line.

    python run.py                          # serve at http://127.0.0.1:5000
    python run.py serve --phones           # also reachable from your phones over Tailscale
    python run.py serve --host 0.0.0.0     # listen on every network (Docker); or set BUDGET_HOST
    python run.py hash-password            # a BUDGET_PASSWORD_HASH for the optional login
    python run.py import-excel data/source/budget.xlsx [--through 2024-12-31]
    python run.py import-csv path/to/export.csv --account "Rewards Card" [--kind credit]
    python run.py simplefin-setup <setup token>          # one-time, from bridge.simplefin.org
    python run.py simplefin-map <id> --account "..."     # map each account simplefin-setup listed
    python run.py simplefin-sync                         # pull since each account's last sync (daily)
    python run.py simplefin-status                       # when each account was last pulled
    python run.py demo [--dir demo-data]                 # a made-up household to try the app on
    python run.py simplefin-unmap <id>                   # stop syncing one (an old id after reconnecting)
"""
import argparse
import os
import sys
import webbrowser
from datetime import date, datetime
from pathlib import Path

# Always run inside the app's own environment (.venv), whichever "python" was typed: a conda or
# system Python won't have Flask.
_VENV_PYTHON = Path(__file__).resolve().parent / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
if _VENV_PYTHON.exists() and Path(sys.prefix).resolve() != _VENV_PYTHON.parent.parent.resolve():
    os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON), *sys.argv])

from budget import create_app, personal, simplefin_import
from budget.csv_import import flips_sign, parse_csv, replace_spreadsheet_rows, store_transactions
from budget.db import connect
from budget.excel_import import ensure_account, import_workbook


def print_phone_address(port):
    import shutil
    import subprocess

    exe = shutil.which("tailscale") or next(
        (p for p in (r"C:\Program Files\Tailscale\tailscale.exe",
                     "/Applications/Tailscale.app/Contents/MacOS/Tailscale") if Path(p).exists()),
        "tailscale",
    )
    try:
        ip = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True, timeout=10).stdout.split()[0]
        name = subprocess.run([exe, "status", "--self", "--peers=false"], capture_output=True, text=True,
                              timeout=10).stdout.split()
        host = name[1] if len(name) > 1 else ip
        print(f"On your phones (Tailscale on): http://{host}:{port}  or  http://{ip}:{port}")
    except (OSError, IndexError, subprocess.SubprocessError):
        print("Tailscale isn't running on this computer yet, so phones can't reach the app. See README: 'Optional: open it on your phone'.")


def _sf_name(org, label):
    return f"{org} - {label}" if org else (label or "")


def _sf_error(e):
    """A SimpleFIN errlist entry ({code, msg, ...}) or an old-style error string, for the log."""
    return f"{e.get('msg')} [{e.get('code')}]" if isinstance(e, dict) else str(e)


def build_demo(args):
    """The demo lives in its own folder; this refuses to go anywhere near the real data folder."""
    from budget import ROOT, demo

    target = Path(args.dir).expanduser().resolve()
    real = Path(os.environ.get("BUDGET_DATA_DIR", ROOT / "data")).expanduser().resolve()
    if target == real or real in target.parents or target in real.parents:
        raise SystemExit(f"Refusing: {target} is (or holds) your real data folder. Pick another --dir.")
    db = target / "budget.db"
    if db.exists():
        if not args.replace:
            raise SystemExit(f"{db} already exists. Use --replace to rebuild the demo there.")
        if not (target / ".ledger-demo").exists():
            raise SystemExit(f"{target} wasn't made by run.py demo; not replacing anything in it.")
        db.unlink()
    target.mkdir(parents=True, exist_ok=True)
    (target / ".ledger-demo").write_text("Made by run.py demo: made-up data, safe to delete.\n")
    previous = os.environ.get("BUDGET_DATA_DIR")
    os.environ["BUDGET_DATA_DIR"] = str(target)
    try:
        app = create_app()
        conn = connect(app.config["DATABASE"])
        got = demo.build(conn)
        conn.close()
    finally:  # point back at the real data folder
        if previous is None:
            os.environ.pop("BUDGET_DATA_DIR", None)
        else:
            os.environ["BUDGET_DATA_DIR"] = previous
    print(f"Demo household in {target}: {got['transactions']} made-up transactions since {got['from']}.")
    print(f"Try it (your own app keeps running on 5000):  BUDGET_DATA_DIR={args.dir} python run.py serve --port 5001")


def main():
    parser = argparse.ArgumentParser(description="BalancePoint: a local personal budget app")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="run the web app (default)")
    serve.add_argument("--port", type=int, default=5000)
    serve.add_argument("--no-browser", action="store_true")
    serve.add_argument("--phones", action="store_true",
                       help="also answer devices on your Tailscale network (nobody else, even on the same Wi-Fi)")
    serve.add_argument("--host", default=os.environ.get("BUDGET_HOST") or None,
                       help="address to listen on, e.g. 0.0.0.0 in Docker (default: BUDGET_HOST, else 127.0.0.1, "
                            "or every network with --phones). Who gets answered is still up to the trust check.")
    sub.add_parser("hash-password", help="print a BUDGET_PASSWORD_HASH for the optional login (README: Security)")
    excel = sub.add_parser("import-excel", help="import an old budget workbook (see [spreadsheet] in personal.toml)")
    excel.add_argument("path")
    excel.add_argument("--through", help="ignore ledger rows after this date (YYYY-MM-DD)")
    csv_cmd = sub.add_parser("import-csv", help="import a bank CSV export")
    csv_cmd.add_argument("path")
    csv_cmd.add_argument("--account", required=True)
    csv_cmd.add_argument("--kind", default="checking", choices=["checking", "credit", "savings"])
    csv_cmd.add_argument("--purchases-positive", action="store_true",
                         help="this bank lists purchases as positive amounts (Amex, Discover); remembered on the account")
    bj = sub.add_parser("import-blackjack", help="import the blackjack tracker workbook (sessions, research, training); keeps sessions logged in the app")
    bj.add_argument("path")
    bj.add_argument("--account", default="Blackjack Bankroll")
    sf_setup = sub.add_parser("simplefin-setup", help="claim a SimpleFIN Bridge setup token (one-time, from bridge.simplefin.org)")
    sf_setup.add_argument("token")
    sf_map = sub.add_parser("simplefin-map", help="map a SimpleFIN account (its id, printed by simplefin-setup/-sync) to a local account")
    sf_map.add_argument("external_id")
    sf_map.add_argument("--account", required=True)
    sf_map.add_argument("--from", dest="sync_from",
                        help="first day to take transactions from (default: the day after the account's latest one)")
    mode = sf_map.add_mutually_exclusive_group()
    mode.add_argument("--transactions", action="store_true", default=None,
                      help="sync transactions too (default for checking, savings, credit and cash accounts)")
    mode.add_argument("--balance-only", dest="transactions", action="store_false",
                      help="sync only the balance (default for investments, retirement, HSA, loans)")
    sf_map.set_defaults(transactions=None)
    sf_unmap = sub.add_parser("simplefin-unmap", help="stop syncing a SimpleFIN account (e.g. an old id after reconnecting a bank)")
    sf_unmap.add_argument("external_id")
    sf_sync = sub.add_parser("simplefin-sync", help="pull balances/transactions for every mapped SimpleFIN account")
    sf_sync.add_argument("--since", help="pull from this date instead of each account's last sync (YYYY-MM-DD)")
    sub.add_parser("simplefin-status", help="when each SimpleFIN account was last pulled")
    demo = sub.add_parser("demo", help="build a made-up household in its own folder (never your real data) to try the app")
    demo.add_argument("--dir", default="demo-data", help="folder for the demo (default: demo-data)")
    demo.add_argument("--replace", action="store_true", help="rebuild it if that folder already has a demo")
    args = parser.parse_args()

    if args.command == "demo":
        return build_demo(args)
    if args.command == "hash-password":
        from getpass import getpass

        from werkzeug.security import generate_password_hash

        password = getpass("Password: ")
        if not password or password != getpass("Again: "):
            raise SystemExit("The two didn't match (or were empty); nothing printed.")
        return print(generate_password_hash(password))

    app = create_app()
    data_dir = Path(app.config["DATABASE"]).parent
    if args.command == "import-excel":
        conn = connect(app.config["DATABASE"])
        through = date.fromisoformat(args.through) if args.through else None
        result = import_workbook(conn, args.path, through, personal.load(app.config["PERSONAL_CONFIG"]))
        if not result["configured"]:
            raise SystemExit("Nothing to read: add a [spreadsheet] section to data/personal.toml "
                             "(personal.example.toml shows the format).")
        print(f"Imported through {result['through']}")
        print(f"  dictionary entries: {result['rules_added']}")
        for account, read, added in result["ledger"]:
            print(f"  {account}: {added} new of {read}")
        sorted_by = result["hand_sorted"]
        print(f"  matched your spreadsheet categories: {sum(sorted_by.values())} "
              f"({sorted_by.get('formula', 0)} by formula, {sorted_by.get('color', 0)} by color), "
              f"{result['recategorized']} changed")
        print(f"  month-end balances: {result['balances']}")
        if result["loans_added"]:
            print(f"  loan schedules set up from balance formulas: {result['loans_added']}")
        if result["skipped_for_bank_data"]:
            print(f"  skipped {result['skipped_for_bank_data']} entries on dates bank exports already cover")
        conn.close()
    elif args.command == "import-blackjack":
        from budget.blackjack import import_workbook as import_blackjack

        conn = connect(app.config["DATABASE"])
        account_id = ensure_account(conn, args.account, "cash")
        got = import_blackjack(conn, args.path, account_id)
        conn.commit()
        conn.close()
        print(f"{args.account}: {got['sessions']} sessions ({got['tables']} tables), net {got['net']:+,.2f}; "
              f"{got['research']} research rows, {got['notes']} notes, {got['training']} practice sessions. "
              "Sessions logged in the app were kept.")
    elif args.command == "import-csv":
        conn = connect(app.config["DATABASE"])
        account_id = ensure_account(conn, args.account, args.kind)
        if args.purchases_positive:
            conn.execute("UPDATE accounts SET csv_flip_sign = 1 WHERE id = ?", (account_id,))
        text = Path(args.path).read_text(encoding="utf-8-sig", errors="replace")
        parsed = parse_csv(text, flip_sign=flips_sign(conn, account_id))
        _, read, added, _, _ = store_transactions(conn, account_id, Path(args.path).name, parsed)
        replaced, carried = replace_spreadsheet_rows(conn, account_id, parsed, Path(args.path).name)
        conn.commit()
        conn.close()
        print(f"Added {added} of {read} transactions to {args.account}"
              + (f"; replaced {replaced} spreadsheet entries for the same dates" if replaced else "")
              + (f", carrying their categories to {carried} bank rows" if carried else ""))
    elif args.command == "simplefin-setup":
        try:
            access_url = simplefin_import.claim_setup_token(args.token)
            simplefin_import.save_access_url(data_dir, access_url)
            accounts, errors = simplefin_import.list_accounts(access_url)
        except simplefin_import.SimpleFinError as exc:
            raise SystemExit(str(exc))
        print(f"Connected; access saved to {simplefin_import.access_url_path(data_dir)}.")
        print('Map each account below with: run.py simplefin-map <id> --account "Local Account Name"')
        for external_id, org, label, balance in accounts:
            print(f"  {external_id}  {_sf_name(org, label)}  (balance {balance})")
        for e in errors:
            print(f"  Bridge says: {_sf_error(e)}")
    elif args.command == "simplefin-map":
        conn = connect(app.config["DATABASE"])
        row = conn.execute("SELECT id FROM accounts WHERE name = ?", (args.account,)).fetchone()
        if not row:
            raise SystemExit(f"No account named {args.account!r}. Add it on the Accounts page first.")
        sync_from = date.fromisoformat(args.sync_from).isoformat() if args.sync_from else None
        transactions, sync_from = simplefin_import.map_account(conn, args.external_id, row["id"], args.transactions, sync_from)
        conn.commit()
        conn.close()
        print(f"{args.external_id} -> {args.account}: "
              + (f"transactions from {sync_from} and the balance" if transactions else "balance only"))
    elif args.command == "simplefin-unmap":
        conn = connect(app.config["DATABASE"])
        gone = conn.execute("DELETE FROM simplefin_accounts WHERE external_id = ?", (args.external_id,)).rowcount
        conn.commit()
        conn.close()
        print(f"Stopped syncing {args.external_id}" if gone else f"{args.external_id} wasn't mapped")
    elif args.command == "simplefin-sync":
        access_url = simplefin_import.load_access_url(data_dir)
        if not access_url:
            raise SystemExit("Not connected yet: run `run.py simplefin-setup <token>` first (README: 'Automatic bank sync').")
        conn = connect(app.config["DATABASE"])
        since = date.fromisoformat(args.since) if args.since else None
        try:
            report = simplefin_import.sync(conn, access_url, date.today(), since)
        except simplefin_import.SimpleFinError as exc:
            conn.close()
            raise SystemExit(f"{datetime.now():%Y-%m-%d %H:%M} SimpleFIN sync FAILED, nothing changed: {exc}")
        conn.commit()
        conn.close()
        print(f"{datetime.now():%Y-%m-%d %H:%M} SimpleFIN sync, {report['start']} through {report['end']}"
              f" ({(report['end'] - report['start']).days + 1} days)")
        shared = {a["account"] for a in report["accounts"] if sum(b["account"] == a["account"] for b in report["accounts"]) > 1}
        for a in report["accounts"]:
            balance = f", balance {a['balance']:,.2f}" if a["balance"] is not None else ""
            if a["account"] in shared:  # several bank accounts feed this one; its balance is their total
                a["account"] = f"{a['account']} [{a['label']}]"
            problem = "" if a["ok"] else "  (bank reported a problem: will retry these dates next run)"
            print(f"  {a['account']}: {a['added']} new of {a['read']}{balance}{problem}")
            if a.get("pending") or a.get("settled") or a.get("dropped"):
                print(f"    pending from last month: {a['pending']} added, {a['settled']} posted and settled, "
                      f"{a['dropped']} never posted and dropped")
        for e in report["errors"]:
            print(f"  Bridge says: {_sf_error(e)}")
        for name in report["missing"]:
            print(f"  {name}: mapped, but the Bridge didn't return it (removed at bridge.simplefin.org?)")
        for s in report["stale"]:
            print(f"  STALE: {s['account']} last pulled {s['last_synced'] or 'never'}")
        from budget import budgeting

        check = budgeting.pace(connect(app.config["DATABASE"]))
        if check and check["spent"] is not None:
            print(f"  Pace: day {check['day']} of {check['days']}, flexible ${check['spent']:,.0f} of ${check['target']:,.0f} "
                  f"({'+' if check['ahead'] >= 0 else '-'}${abs(check['ahead']):,.0f} against the pace)")
        for w in (check or {}).get("warnings", []):
            print(f"  WARNING: {w}")
        if report["unmapped"]:
            print('  Not mapped yet (run.py simplefin-map <id> --account "Local Account Name"):')
            for external_id, org, label in report["unmapped"]:
                print(f"    {external_id}  {_sf_name(org, label)}")
    elif args.command == "simplefin-status":
        conn = connect(app.config["DATABASE"])
        rows = simplefin_import.status(conn)
        conn.close()
        if not simplefin_import.load_access_url(data_dir):
            print("Not connected: run `run.py simplefin-setup <token>` first.")
        for s in rows:
            mode = f"transactions from {s['sync_from']}" if s["transactions"] else "balance only"
            flag = "  STALE" if s["stale"] else ""
            print(f"{s['account']:<28} last pulled {s['last_synced'] or 'never':<10}  {mode}  [{_sf_name(s['org'], s['label'])}]{flag}")
    else:
        port = getattr(args, "port", 5000)
        phones = getattr(args, "phones", False)
        if phones:
            print_phone_address(port)
        if not getattr(args, "no_browser", False):
            webbrowser.open(f"http://127.0.0.1:{port}")
        # With --phones the server listens on every network, and the app itself turns away anything
        # that isn't this computer or a Tailscale device (budget.trusted).
        host = getattr(args, "host", None) or os.environ.get("BUDGET_HOST") or ("0.0.0.0" if phones else "127.0.0.1")
        app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
