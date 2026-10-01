# Changelog

Notable changes to BalancePoint. The format follows [Keep a Changelog](https://keepachangelog.com/),
and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Open-source housekeeping: `CONTRIBUTING.md`, `SECURITY.md`, this changelog, issue and pull request
  templates.
- `scripts/check_no_personal_data.py`: fails if tracked files include personal data files, look like
  secrets, or contain words from a local, untracked `.personal-words` deny-list. Runs in CI;
  `scripts/install-git-hooks.sh` installs it as an opt-in pre-commit hook.
- `scripts/prepare_public_repo.sh`: builds a public copy with rewritten author identities.

## [0.1.0] - first public release (unreleased)

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
