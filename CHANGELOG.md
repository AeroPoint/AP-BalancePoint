# Changelog

Notable changes to BalancePoint. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Projection, "Retirement: does the money last?": yearly spending from a retirement month, less incomes
  like Social Security or a pension from their own months, through a last month, in every simulated future;
  the share of futures the money lasts in, what's left, and what would make it last in half, 3 of 4 or
  9 of 10 of them.

## [0.3.0] - 2026-11-22

### Added
- Projection (in the menu): net worth 1 to 40 years out. Each account grows at its own rate with a
  "swings" figure (volatility), from 3+ years of its own balances or a default for its kind, per account or
  one for every investment; the shaded range is where 8 in 10 of 300 simulated futures land. Paycheck
  contributions continue, loans pay down, the Plan can run through it (and it says when the spending account
  can't be kept topped up), and it can show today's dollars.
- Projection: each year shows the chance of being below today, and "How bumpy the ride is" gives the
  typical and bad-case fall from a high, the chance of being behind over time, and N0.
- Projection, "What would it take?": the monthly saving that reaches an amount by a date (today's or future
  dollars) across the accounts you pick, split across accounts by shares, on the expected path (every rate
  exactly, which is optimistic) or in half, 3 of 4 or 9 of 10 futures. The answer can be a flat amount, a %
  of income (with a yearly raise), or a % of what each month leaves over, and the same goal is answered for
  other dates 5 years apart.
- Projection: money going in per account (an amount, a yearly raise, from and until), and lump sums: a
  property sale that pays off its loan, or a windfall.
- Plan through: the Plan can run up to 40 years ahead to a month you pick (still the next 12 months by
  default), shown monthly for the first year and then yearly. After it, the Projection carries the planned
  month on as it is now, without the changes.
- Plan growth: regular income and planned spending can grow by a % a year (raises, inflation), and each
  expected change by its own % from its first month.
- Plan by a savings goal: an amount or a % of income put into an account first each month, with the
  flexible target what's left. The Overview and the month forecast judge the month on that target.

### Fixed
- Projection: paycheck retirement contributions are averaged over the months since they were set up, not
  always 12.

## [0.2.0] - 2026-11-18

### Added
- Tax time: *Choose what's on Tax time* sets, for each category, whether it's left out, counted by its ticked
  transactions, or counted whole with nothing to tick (a donations category, say). "Checkbox items" is now
  "Tax items".
- Tax time: *What counts as the rental* picks the rental's checkbox, its rent categories and its mortgage
  categories, so a rental works whatever they're called (it used to need "Rental property", "Rental…" and
  "Mortgage" names). The section shows even before a rental has anything in it, to set it up.
- Email notifications on a Mac can use a Keychain item's password (save `keychain:<item name>`),
  read each time a message goes out, so an app password shared with other jobs is renewed in one place.
- Manage data (in the menu): every bulk erase in one place, each saying what it erases and what stays:
  stored Amazon items, one import, or Erase everything (type ERASE). A restore point (a copy of the whole
  database) is saved before each, and the page can put any of the last 10 back. The scheduled backup
  (`run.py backup-check`, used by `sync-mac.sh`) won't commit a database that lost most of its
  transactions unless Erase everything was used. Removing an import and erasing Amazon items moved there
  from Upload data.
- A Raspberry Pi for someone else: `scripts/make-pi-image.sh` makes a Raspberry Pi OS image that sets
  itself up on first boot (Tailscale, the app on port 80, the daily sync, security updates) and moves to
  each new release weekly, going back if the new one doesn't start (`pi/`).
- Amazon orders (Upload data): upload the ZIP from Amazon's Request Your Data and each Amazon charge shows
  what it paid for, on Categorize and Transactions (and Transactions search finds item names). Charges are
  matched by amount and date, allowing for a state delivery fee. Categorize suggests a category for products
  bought before, and starts the Split form with each category's share for mixed orders.
  `run.py import-amazon <file>` does the same from the command line. The orders CSV from the Amazon
  Order History Reporter browser extension works too, for a quick month or two: each order is matched
  whole, at its total, and the extension's items CSV then shares that total out among its items by price.
- Month forecast on the Overview, replacing the pace check: flexible and fixed spending so far plus
  what's known to still be coming (regular charges from the Recurring page not yet charged this month,
  and expected changes from the Plan page), against their targets, with the flexible money left per day.
  It warns when a forecast passes its target or the day's allowance is well under the plan's. The sync
  log and notifications carry its line.
- Plan changes can say which category they come out of: a fixed or flexible one counts in that month's
  forecast instead of the projection.

### Fixed
- A ticked charge moved to a category with a different checkbox no longer arrives already ticked there.

## [0.1.1] - 2026-10-31

### Added
- Notifications: a weekly recap (pick the day), daily or monthly as well as only-on-warnings, and set up in
  the browser (Bank sync → set up notifications); `[notify]` in `personal.toml` still wins.
- Tax time: questions for your accountant, written down per tax year and checked off when answered.
- Tax time: a Rental section, rental income against ticked costs, with mortgage payments split into
  interest, principal and escrow from the loan's terms (principal isn't a cost).
- Merchant dictionary: "Sort by hand" (a merchant's charges always land in Categorize) and "Can be split"
  (split a charge across categories: type the amounts you know and the rest is shared in proportion).
- Appearance page: light, dark or automatic, and an accent color that also tints the background, remembered
  per device.
- Bank sync retries: the Mac's schedule tries again hourly until 21:00 when the 6:00 sync fails
  (`run.py simplefin-synced-today` for other schedulers), a failure is reported once a day, and the
  Overview says when the bank data is behind.
- Pending charges: show this month's on Transactions (marked Pending, out of its totals) and choose whether
  the pace check counts them, both on the Bank sync page.

### Fixed
- Phones: the Accounts and Upload pages no longer come out wider than the screen (the phone zoomed the whole
  page out), and no page can get wider than the screen.
- Phones: the bottom tab bar steps aside while typing and is re-pinned afterwards, instead of scrolling with
  the page or staying stuck partway up the screen (iOS).
- CI: the Docker build retries when Docker Hub has a hiccup, and the bank sync test passes at any hour.

## [0.1.0] - 2026-10-04

The first public release. The last changes before it are listed first; everything the app does
follows under "What's in 0.1.0".

### Added
- **Recurring** page: bills and subscriptions found in your spending, with monthly and yearly cost,
  next expected date, and flags for new ones, price increases, missed and ended charges.
- **Tax time** page: tax-checkbox items, business profit, income and "ask your accountant" notes for a
  year, with CSV downloads and a print layout.
- Optional notifications after the bank sync (email or ntfy), off unless set up in `[notify]`.
- Plan: a "keeping at least" amount for each backup account.
- The pace check counts this month's still-pending charges.
- Bank sync: use a new setup token while connected.
- Open-source housekeeping: `CONTRIBUTING.md`, `SECURITY.md`, this changelog, issue and pull request
  templates.
- `scripts/check_no_personal_data.py`: fails if tracked files include personal data files, look like
  secrets, or contain words from a local, untracked `.personal-words` deny-list. Runs in CI;
  `scripts/install-git-hooks.sh` installs it as an opt-in pre-commit hook.
- `scripts/prepare_public_repo.sh`: builds a public copy with rewritten author identities.
- Pace check-in on the Overview (and in the sync log): flexible spending against the month so far,
  with warnings when ahead of pace, over target, or the spending account is under its cushion.
- CSV import: a per-account "lists purchases as positive amounts" setting (Amex, Discover and many
  card exports), and headerless exports.
- `[features] business`: the Business page is optional, on automatically once an account has its own
  categories.
- Scheduling the daily bank sync on Linux (cron, systemd), Windows (Task Scheduler) and Docker;
  `MAINTAINING.md` for maintainer-only notes.

### Changed
- The font (Libre Franklin, SIL Open Font License) is bundled, so pages contact no outside service.
- The built-in merchant dictionary is national merchants only; household-specific entries were
  removed. Existing databases keep the rules they already have.
- The bank card payment rule is named "Card Payment" on new installs.
- `start-mac.sh` finds Python 3.11+ on Intel Macs and from python.org, not only Apple-silicon Homebrew.
- Upload data shows a quiet note instead of an error when no old spreadsheet is configured.

### Fixed
- The Mac scripts keep their executable bit in git (a merge had dropped it, so launchd couldn't
  start the app).

## What's in 0.1.0

Everything up to the first public release.

### Getting data in
- Bank CSV import from any bank with date, description and amount (or debit/credit) columns; US Bank
  exports understood best, including their merchant category codes. Re-uploading overlapping ranges
  skips duplicates.
- Daily bank sync through SimpleFIN Bridge: transactions and balances, catch-up after missed days,
  stale-account warnings, month-end pending charges counted in the month they happened, and synced
  charges counted in the month they happened when they post later.
- Import of an old hand-kept budget spreadsheet (ledger, hand-sorted categories, balances,
  investments), described in `data/personal.toml`.
- Hand-entered transactions, and reconciling bank exports with spreadsheet history.

### Categorizing
- Merchant dictionary that cleans bank descriptions, "always" rules, category keywords and card-type
  guesses; hand-picked categories are never overwritten.
- Effective dates: "count on nearest 1st" categories (rent, mortgage) and dates set by hand.
- Category checkboxes for tax time (e.g. "Rental property", "Should be FSA/HSA").

### Reports and planning
- Money flow by month and by year, spending groups (fixed, flexible, non-monthly), one-off purchases,
  and a month scorecard against a flexible-spending target.
- Paycheck retirement savings (401(k) and employer match) counted as saving.
- Plan page: the next 12 months for the spending account, drawing from backup accounts in order.
- Accounts and net worth across open and closed accounts, each month pinned to its 1st; accounts
  held for someone else kept apart; loan schedules.
- Business accounts with their own categories, and a Business page with income, costs and profit.
- Optional Blackjack bankroll page (sessions, expected value, research, training), switched on in
  `[features]`.

### Running it
- Phone and desktop layouts, bottom tab bar, home-screen icon; phone access over Tailscale only.
- Always-on Mac setup: `install-mac.sh`, launchd auto-start, daily 6:00 sync with `data/` committed
  before and after.
- `python run.py demo`: a made-up demo household (Pat and Sam Smith) in its own `demo-data/` folder.
- Test suite on made-up data, run by GitHub Actions on macOS, Windows and Linux.

[Unreleased]: https://github.com/AeroPoint/AP-BalancePoint/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/AeroPoint/AP-BalancePoint/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/AeroPoint/AP-BalancePoint/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/AeroPoint/AP-BalancePoint/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/AeroPoint/AP-BalancePoint/releases/tag/v0.1.0
