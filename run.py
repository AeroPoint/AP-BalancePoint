"""Start the budget app, or run a one-off import from the command line.

    python run.py                          # serve at http://127.0.0.1:5000
    python run.py import-excel data/source/budget.xlsx [--through 2024-12-31]
    python run.py import-csv path/to/export.csv --account "US Bank Credit" [--kind credit]
"""
import argparse
import webbrowser
from datetime import date
from pathlib import Path

from budget import create_app, personal
from budget.csv_import import parse_csv, replace_spreadsheet_rows, store_transactions
from budget.db import connect
from budget.excel_import import ensure_account, import_workbook


def main():
    parser = argparse.ArgumentParser(description="Personal budget app")
    sub = parser.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="run the web app (default)")
    serve.add_argument("--port", type=int, default=5000)
    serve.add_argument("--no-browser", action="store_true")
    excel = sub.add_parser("import-excel", help="import an old budget workbook (see [spreadsheet] in personal.toml)")
    excel.add_argument("path")
    excel.add_argument("--through", help="ignore ledger rows after this date (YYYY-MM-DD)")
    csv_cmd = sub.add_parser("import-csv", help="import a bank CSV export")
    csv_cmd.add_argument("path")
    csv_cmd.add_argument("--account", required=True)
    csv_cmd.add_argument("--kind", default="checking", choices=["checking", "credit", "savings"])
    args = parser.parse_args()

    app = create_app()
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
    elif args.command == "import-csv":
        conn = connect(app.config["DATABASE"])
        account_id = ensure_account(conn, args.account, args.kind)
        text = Path(args.path).read_text(encoding="utf-8-sig", errors="replace")
        parsed = parse_csv(text)
        _, read, added, _, _ = store_transactions(conn, account_id, Path(args.path).name, parsed)
        replaced, carried = replace_spreadsheet_rows(conn, account_id, parsed, Path(args.path).name)
        conn.commit()
        conn.close()
        print(f"Added {added} of {read} transactions to {args.account}"
              + (f"; replaced {replaced} spreadsheet entries for the same dates" if replaced else "")
              + (f", carrying their categories to {carried} bank rows" if carried else ""))
    else:
        port = getattr(args, "port", 5000)
        if not getattr(args, "no_browser", False):
            webbrowser.open(f"http://127.0.0.1:{port}")
        app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    main()
