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

### On your phone

The app has no login, so it only answers this computer, and, when started with
`start-phones.bat` (`run.py serve --phones`), devices on your own [Tailscale](https://tailscale.com)
network. Anyone else, even on the same Wi-Fi, gets "Forbidden".

1. Install Tailscale on this computer and sign in (Google or Microsoft account is fine).
2. Install the Tailscale app on each phone and sign in with the same account (or invite your
   partner's account from the Tailscale admin page).
3. Start the app with `start-phones.bat`. It prints the phone address, like
   `http://your-pc-name:5000`. The first time, allow Python through the Windows firewall on
   **private** networks.
4. Open that address on the phone and use *Add to Home Screen* (Safari's share menu, or Chrome's
   menu) to get a Ledger icon that opens full screen.

The computer has to be on with the app running. For always-on access, run the app on a small
always-on machine at home instead, the same way.

### On a Mac (always-on home server)

1. Copy the whole `Budget` folder, including `data/`, to somewhere **outside** Desktop, Documents
   and Downloads (macOS blocks background apps there), e.g. `~/Budget`. Stop the app on any other
   computer first, so only one copy of the database is in use.
2. Install Tailscale for Mac and sign in to the same account. In System Settings → Energy, turn on
   *Prevent automatic sleeping* and *Start up automatically after a power failure*.
3. In Terminal: `brew install python@3.12`, then `cd ~/Budget && chmod +x *.sh && ./install-mac.sh`.
   The app gets its own environment in `.venv`, built from Homebrew's Python so it doesn't depend on
   conda or whatever `python` means in your shell; `python run.py ...` switches into it by itself.
   The installer starts the app, and makes it start again at every login and after a
   crash. It prints the address for phones and other computers, e.g. `http://mac-mini:5000`.
   Allow incoming connections if macOS asks.
4. Log: `data/server.log`. Remove the auto-start (and the daily bank sync) with
   `./install-mac.sh remove`; run it by hand with `./start-mac.sh`.

For the app to come back after a power cut, the Mac needs to log in to that account on its own
(System Settings → Users & Groups → automatic login), since it starts at login.

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
- **Automatic bank sync:** see below.

### Automatic bank sync (SimpleFIN Bridge)

[SimpleFIN Bridge](https://bridge.simplefin.org) is a read-only bank-data service: $1.50 a month or
$15 a year for up to 25 institutions, paid to them directly. The app pulls from it once a day, so bank
CSVs aren't needed for the accounts it covers.

1. Sign up at bridge.simplefin.org, connect each bank there, and create a **setup token**.
2. `run.py simplefin-setup <token>`. The token works once; the long-lived access it's exchanged for
   is saved to `data/simplefin-access-url` (only your user can read it, and `data/`'s own git repo
   ignores it). It lists every account the Bridge sees, each with an id.
3. For each: `run.py simplefin-map <id> --account "Joint Checking"`. The account must already exist on
   the Accounts page.
   - Checking, savings, credit card and cash accounts sync **transactions and the balance**.
     Transactions start the day after the account's latest one, so nothing CSVs already brought in is
     doubled; `--from YYYY-MM-DD` picks another day. Once an account syncs, stop uploading its CSVs:
     the bank's text in a CSV can differ from SimpleFIN's, so the same charge could come in twice.
   - Everything else (brokerage, retirement, HSA, loans) syncs **the balance only**, so a 401(k)
     contribution isn't counted as income on top of *Paycheck retirement savings*. `--transactions` or
     `--balance-only` overrides.
4. `run.py simplefin-sync` pulls everything new; `run.py simplefin-status` shows when each account was
   last pulled. Reconnecting a bank at the Bridge gives its accounts new ids: the log then
   shows the old id as "mapped, but the Bridge didn't return it" and the new one as not mapped;
   `run.py simplefin-unmap <old id>` and map the new one.
5. Loans: a synced lender balance replaces the loan schedule from its date on, while it's less than
   35 days old, so the schedule still covers history and takes over again if the sync stops.

On the Mac, `install-mac.sh` runs `sync-mac.sh` **every day at 6:00** (after the banks' overnight
posting) and again whenever the Mac starts up. It commits `data/` before and after, so a bad sync is
one `git revert` away. Log: `data/simplefin-sync.log`.

Missed days fill in by themselves. Each account remembers its last good pull, and the next run starts
from the oldest of those, less 5 days for anything that posted late. So after a week with the Mac off
or offline, or with a bank that needed signing in again at the Bridge, the next run pulls the whole
gap. An account whose bank reported a problem keeps its old date until a clean pull. The log flags any
account not pulled in 2+ days as **STALE**; the usual fix is signing in to that bank again at
bridge.simplefin.org.

Synced balances count like ones you type: the latest one in a month is that month's balance. A synced
balance replaces one you typed only when the bank's date is newer. Pending transactions wait until
they post. The Bridge asks for no more than 24 requests a day; a daily run uses one (more only when
catching up past 90 days).

## Accounts and net worth

*Accounts* lists every account with its type, opened and closed month, activity and latest
balance. Closing an account keeps its history in every report; it just stops asking for
balances afterwards. Accounts that have gone quiet get a "Mark closed" suggestion. If the same
account comes in under two names, merge them.

*Net worth* shows where money sits in a month, own/owe/net over time, and each account's
balance history. Tick accounts on or off, or use a preset (cash only, investments only, leave
out home and cars). Accounts typed *Held for someone else* (a child's 529) sit in their own group and
stay out of every total until you tick them. Balances are for a day. For accounts with transactions, the app estimates
today's balance from the last one entered plus everything that posted since; type the bank's
number now and then to correct it. Accounts without transactions carry their last balance forward.
Loans with fixed terms (*Accounts → Loan schedules*) are calculated every month instead, and the
spreadsheet import sets them up from an `=-FV(rate/12, DATEDIF(...), PMT(...), amount)` balance formula.
Overview and Year trends have the same account filter for spending and income.

A business account (*Accounts → Business accounts*) has its own categories: everything on it goes to
its income category (money in) or expense category (money out), whatever the merchant rules say.
Transfers to and from your other accounts stay Transfers, and a category picked by hand on one
transaction (a personal charge made on the business card) stays.
The **Business** page shows each business account's income, costs and profit by month and year, from
its two categories on any account (a business cost paid on a household card counts), with the money you
moved in and out of the business account shown apart.

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
shortfall comes from backup accounts in the order you pick (say savings, then a brokerage account), and
the page shows when that starts and how much each one gives.

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
upload instead). The hand-entry form starts on your first cash account, so keep "Cash on hand"
ahead of other cash accounts.

### Blackjack bankroll

The **Bankroll** page is the blackjack tracker: every session (a casino visit, with each table's game,
rules, conditions, hours and EV), what actually happened next to what was expected, yearly totals like
the tracker's own (travel is miles × a $/mile rate plus flights and room and board), a running-result
chart, research by trip (casinos, rules, EV, bet spreads, directions) and a training log.

- **Log a session** on the page, on the phone at the casino if you like. Picking a casino you've played
  fills in its last game. Add a table per game played during the visit.
- Each session's result is a transaction in the *Blackjack Bankroll* cash account, Blackjack category,
  and follows the session when it's edited or deleted. Money moving between it and the bank is a
  Transfer. If the account drifts from the cash you actually hold, enter what you hold on Net worth.
- The old workbook (`data/source/Blackjack Tracker.xlsx`) was imported once: *Upload data → Blackjack
  tracker*, or `python run.py import-blackjack "Blackjack Tracker.xlsx"`. Importing again replaces only
  what came from a workbook; sessions, research and practice added in the app stay.

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

## Working on the code

- **Personal data stays in `data/`**, which is git-ignored here and is its own git repo. Code,
  comments, examples and commit messages use made-up names (Pat Smith, ACME CORP). Household
  rules go in `data/personal.toml`, not in `seed.py`. Private notes and to-dos: `data/TODO.md`.
- **Schema changes** go in `db.migrate()` so existing databases upgrade on startup. One-time data
  migrations are gated on `PRAGMA user_version` (currently 2: folded categories, spending groups).
- **Reports count transactions on `effective_date`** (a date set by hand, else the nearest 1st for
  "count on nearest 1st" categories, else the bank date); `rules.sync_dates` keeps it current.
- **Balances** come from `balances.AccountLedger`: the latest recorded balance on or before a day,
  plus transactions after it (loan schedules override for loans).
- **Try data changes on a copy** first: copy `data/` somewhere, point `BUDGET_DATA_DIR` at it, and
  run the app or a script against that. Check an account by comparing its ledger balance at month
  ends with the bank export's running balance.

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
  business.py           Business page: income, costs and profit per business account
  blackjack.py          Bankroll page: sessions, research, training; results into a bankroll account
  simplefin_import.py   daily bank sync from SimpleFIN Bridge
  views.py              pages and JSON endpoints
  templates/, static/
data/                   (git-ignored) budget.db, personal.toml, uploads/, source/
```

Set `BUDGET_DATA_DIR` to keep the database somewhere else.
