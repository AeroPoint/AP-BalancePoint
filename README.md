# Ledger

A local, Monarch-style budget app. It imports US Bank CSV exports (and, optionally, an old
hand-kept budget spreadsheet), cleans up merchant names with an editable dictionary,
auto-categorizes transactions, shows money flow by month and by year, and tracks balances
and net worth across accounts, including ones you've closed.

Everything runs on your machine. Data lives in `data/`, which git ignores.

## Run it

Double-click `start.bat`, or:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe run.py            # opens http://127.0.0.1:5000
```

## Personal settings

Anything specific to your household goes in `data/personal.toml`, never in the code:
local merchants, extra categories, category keywords, and how to read an old budget
spreadsheet. Copy `personal.example.toml` to `data/personal.toml` to start. The app reads it at
startup, so restart after editing. Changes you make in the app win over the file.

## Getting data in

- **US Bank CSV:** in the app, open *Upload data*, choose one or more CSV files and
  the account they belong to. Re-uploading overlapping date ranges is safe;
  duplicates are skipped. A bank export replaces entries imported from an old spreadsheet for
  the same account and dates, since the bank's record is the one to keep. The spreadsheet's
  category, date and notes for each entry move to the bank row with the same amount nearest in
  date. To keep spreadsheet months you already checked against the bank, set the account's
  *Bank exports from* month on the Accounts page: export rows before it are left out.
- **Old budget spreadsheet:** describe its layout in the `[spreadsheet]` section of
  `data/personal.toml`, then use *Upload data → Old budget workbook*, or:

  ```powershell
  .venv\Scripts\python.exe run.py import-excel data\source\budget.xlsx
  ```

  Depending on what you map, it brings in:
  - a daily ledger as transaction history, through the file's last-saved date
  - your hand-sorted categories: `=SUM(...)` formulas under summary labels, with cell colors
    used for ledger cells no formula points at
  - merchant names you typed next to a bank export
  - month-end balances from running balance columns and label/value pairs on the ledger sheet
  - current values from an investments sheet

  Running it again is safe. It won't overwrite categories or balances you changed in the app.
- **Command-line CSV import:**
  `run.py import-csv export.csv --account "Checking" --kind checking`

## Accounts and net worth

*Accounts* lists every account with its type, opened and closed month, activity and latest
balance. Closing an account keeps its history in every report; it just stops asking for
balances afterwards. Accounts that have gone quiet get a "Mark closed" suggestion. If the same
account comes in under two names, merge them.

*Net worth* shows where money sits in a month, own/owe/net over time, and each account's
balance history. Tick accounts on or off, or use a preset (cash only, investments only, leave
out home and cars). Balances are for a day. For accounts with transactions, the app estimates
today's balance from the last one entered plus everything that posted since; type the bank's
number now and then to correct it. Accounts without transactions carry their last balance forward.
Loans with fixed terms (*Accounts → Loan schedules*) are calculated every month instead, and the
spreadsheet import sets them up from an `=-FV(rate/12, DATEDIF(...), PMT(...), amount)` balance formula.
Overview and Year trends have the same account filter for spending and income.

## Judging a month, and planning ahead

A single month's savings rate swings with every big purchase, so the Overview judges a month on
**flexible spending** against a monthly target you set together. Each spending category counts as
one of three groups (set on the Categories page):

- **Fixed**: bills that barely change (mortgage, utilities, insurance)
- **Flexible**: day-to-day spending you steer (groceries, dining, shopping, hobbies, gas)
- **Non-monthly**: lumpy costs (projects, travel, taxes)

Mark a big one-time purchase as a **one-off** (on the Transactions page, or from the Overview's
large-charges list) and it's kept out of its group, though it still counts as spending. The Overview
shows which flexible categories ran above or below a usual month (the median of the previous six).

A 401(k) comes out of a paycheck before the bank sees it. Add it under **Accounts → Paycheck
retirement savings** (base pay per paycheck, your %, the employer match) and it counts as saving in
the Overview and in yearly savings rates. For a raise or a new rate, add an entry from that date on.

The **Plan** page projects the account you spend from over the next 12 months: planned income,
fixed and non-monthly spending, the flexible target, and changes you expect (time off work,
overtime, a planned purchase). When the account would drop below the cushion you set, the
shortfall comes from a backup account, and the page shows when that starts and how much it takes.

## Cash

Bank exports only see cash leave the bank. Keep cash you hold onto in its own account (type Cash):

- Cash taken out for one specific purchase (a contractor, a vet bill): categorize the withdrawal
  as that purchase. Nothing else to enter.
- Cash taken out to hold (cash on hand, a bankroll): categorize the withdrawal as Transfer, then add
  the matching money-in on the cash account with *Transactions → Add a transaction by hand*.
  What you spend from it goes there too, with its real category; putting it back is a Transfer.
- Don't enter a purchase plus a "paid in cash" offset: the offset looks like a deposit and hides
  where the money came from.

Transactions added by hand can be deleted from the Transactions page; bank rows can't (remove their
upload instead).

## How naming and categorizing works

Each transaction goes through these steps:

1. **Bank names** (Merchant dictionary): the longest entry whose text appears in the bank
   description sets the clean name and, if it has one, the category. Your edits in the app
   beat `personal.toml`, which beats names learned from a spreadsheet, which beat built-in ones.
2. **Always rules**: choosing “Always put X in Y” pins a merchant to a category.
3. **Category keywords**: words in the clean name, like `Gas` → Auto & Gas.
4. **Card type**: US Bank card exports include a merchant category code; it's used as a
   last-resort guess, marked “guess” in the app.

Zelle and Venmo payments are named after the person, like “Zelle to Pat Smith”, so each person
can have their own category.

Every transaction keeps the bank's date but counts on an effective date. Categories marked
“count on nearest 1st” (rent and mortgage by default) move to the nearest first of the month, and
you can set any transaction's date by hand on the Transactions page.

Categories you pick by hand on a single transaction, and ones hand-sorted in an imported
spreadsheet, are never overwritten by rules. Transfer categories (card payments, moving
money, investing) are excluded from income and spending.

The built-in dictionary of national merchants lives in `budget/seed.py`. New entries there are
added on the next start without touching anything you've edited.

## Layout

```
run.py                  start the server / command-line imports
personal.example.toml   format for data/personal.toml
budget/
  db.py                 SQLite schema and upgrades of older databases
  seed.py               built-in categories, merchant dictionary, card-type map, account types
  personal.py           loads data/personal.toml
  rules.py              name cleanup + rule engine
  csv_import.py         CSV parsing and de-duplicated inserts
  excel_import.py       spreadsheet import (ledger, hand-sorted categories, balances)
  reports.py            monthly/yearly aggregations
  balances.py           account balances on any date, loan schedules
  budgeting.py          month scorecard, paycheck retirement savings, the plan
  views.py              pages and JSON endpoints
  templates/, static/
data/                   (git-ignored) budget.db, personal.toml, uploads/, source/
```

Set `BUDGET_DATA_DIR` to keep the database somewhere else.
