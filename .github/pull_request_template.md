## What and why

<!-- What this changes, and the problem it solves. Link an issue if there is one. -->

## How I tested it

<!-- Tests added or run; anything checked by hand on the demo data (python run.py demo). -->

## Checklist

- [ ] **No personal data**: no real names, transactions, balances, account numbers, bank descriptions,
      databases, spreadsheets, CSVs or access URLs in the code, tests, screenshots or commit messages
      (made-up ones like Pat Smith / ACME CORP only). `python scripts/check_no_personal_data.py` passes.
- [ ] `python -m pytest -q tests` passes, with tests for the change.
- [ ] Existing databases and `personal.toml` files keep working (schema changes are additive, in `db.migrate()`).
- [ ] `CHANGELOG.md` has an entry under "Unreleased" if users would notice the change.
