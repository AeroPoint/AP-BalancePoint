# Contributing to BalancePoint

Thanks for helping. BalancePoint is a local Flask + SQLite budget app: everything runs on the
user's own machine, and their financial data never leaves it. Contributions keep it that way.

## Set up

Python 3.12 is what CI uses (3.11+ should work).

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt      # Windows: .venv\Scripts\python.exe
```

## Run it

```sh
.venv/bin/python run.py                      # http://127.0.0.1:5000, data in data/
```

Work against made-up data rather than your own:

```sh
.venv/bin/python run.py demo                 # builds the Pat and Sam Smith household in demo-data/
BUDGET_DATA_DIR=demo-data .venv/bin/python run.py serve --port 5001
.venv/bin/python run.py demo --replace       # rebuild it
```

## Tests

```sh
.venv/bin/python -m pytest -q tests
.venv/bin/python scripts/check_no_personal_data.py
```

Every test runs in its own temporary data folder on made-up data and never reads `data/`. Add a test
for any behaviour you change. GitHub Actions runs the suite on macOS, Windows and Linux, plus the
personal-data check.

## Personal data never goes in the repo

- `data/`, `demo-data/`, databases, spreadsheets, CSVs and `simplefin-access-url` are git-ignored, and
  `scripts/check_no_personal_data.py` fails CI if one is ever tracked or if tracked text looks like a
  secret (a URL with a password in it, an access key).
- Code, comments, tests, examples, screenshots, issues and commit messages use made-up names:
  **Pat Smith**, **Sam Smith**, **ACME CORP**, `bank.example.com`. Never paste real transactions,
  balances, account numbers or bank descriptions; make up a lookalike instead.
- Household-specific rules belong in `data/personal.toml` (see `personal.example.toml`), not in
  `budget/seed.py`.
- Optional, for your own clone: list words that must never be committed (your real names, street,
  employer, account numbers) one per line in `.personal-words` at the repo root. Git ignores that
  file, and the checker refuses any tracked file containing one of them. `scripts/install-git-hooks.sh`
  installs a pre-commit hook that runs the check (opt-in).

## Changing the code

- **Keep changes additive and backward-compatible.** People run this against years of their own data.
  An upgrade must never lose or silently change it, and existing `personal.toml` files, CLI commands
  and URLs keep working.
- **Schema changes** go in `db.migrate()` in `budget/db.py`, so existing databases upgrade on startup.
  Add columns and tables; don't drop or rename them. One-time data migrations are gated on
  `PRAGMA user_version`.
- Reports count transactions on `effective_date`; balances come from `balances.AccountLedger`.
  See "Working on the code" in the README for more.
- Try data changes on a copy: point `BUDGET_DATA_DIR` at a copied folder.
- Keep the app local-first: no telemetry, no calls to outside services except ones the user sets up
  (SimpleFIN Bridge).

## Pull requests

Small, focused PRs are easiest to review. Describe what changed and how you tested it, add an entry
under "Unreleased" in `CHANGELOG.md` for anything a user would notice, and tick the template's
"no personal data" box only after checking.

Report security problems privately; see [SECURITY.md](SECURITY.md).
