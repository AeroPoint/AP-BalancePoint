# Changelog

Notable changes to BalancePoint. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-04

The first public release. The last changes before it are listed first; everything the app does
follows under "What's in 0.1.0".

### Added
- **Recurring** page: bills and subscriptions found in your spending, with monthly and yearly cost,
  next expected date, and flags for new ones, price increases, missed and ended charges.
- **Tax time** page: tax-checkbox items, business profit, income and "ask your accountant" notes for a
  year, with CSV downloads and a print layout.
- Optional notifications after the bank sync (email or ntfy): when there are warnings, daily, weekly or
  monthly; set up in the browser (Bank sync → set up notifications) or `[notify]`. Off until set up.
- Tax time: questions for your accountant, written down per tax year and checked off when answered.
- Appearance page: light, dark or automatic, and an accent color that also tints the background, remembered
  per device.
- Tax time: a Rental section, rental income against ticked costs, with mortgage payments split into
  interest, principal and escrow from the loan's terms (principal isn't a cost).
- Merchant dictionary: "Sort by hand" (a merchant's charges always land in Categorize) and "Can be split"
  (split a charge across categories: type the amounts you know and the rest is shared in proportion).
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

[Unreleased]: https://github.com/AeroPoint/AP-BalancePoint/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/AeroPoint/AP-BalancePoint/releases/tag/v0.1.0
