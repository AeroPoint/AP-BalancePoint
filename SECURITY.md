# Security

BalancePoint handles personal financial data: transactions, balances, account names and, with bank
sync, a long-lived SimpleFIN Bridge access URL that can read every connected account. Please treat
security problems seriously and report them privately.

## How the app is meant to run

- **Local-first.** Everything lives on your machine in `data/` (an SQLite database, uploads, settings,
  `simplefin-access-url`). Nothing is sent anywhere except the SimpleFIN Bridge requests you set up.
- **No login by default.** The server listens on `127.0.0.1` and answers only this computer. With
  `run.py serve --phones` it listens on every network but the app itself answers only this computer
  and devices on your own Tailscale network; every other address, including others on the same
  Wi-Fi, gets "Forbidden". Anyone who can reach the app can read and
  change everything in it.
- **Do not expose it to the internet** (port forwarding, a public reverse proxy, a tunnel open to
  everyone). If you need remote access, use a private network such as Tailscale.
- `data/simplefin-access-url` is saved readable only by your user. Keep `data/` backups as private as
  your bank statements.

## Reporting a vulnerability

Please **do not open a public issue** for a security problem. Instead use GitHub's private
vulnerability reporting: the repository's **Security** tab, then **Report a vulnerability**.
<!-- TODO(owner): if private reporting is not enabled on the GitHub repo, enable it under
     Settings > Code security, or put a contact address here. -->

Include what you found, how to reproduce it (with made-up data, never your real data), and the
version or commit. You should get a reply within a week. Once fixed, the fix is released and noted in
`CHANGELOG.md`, crediting you if you like.

In scope, for example: anything letting a device outside the allowed addresses reach the app, ways a
crafted CSV, spreadsheet or bank response could run code or read files, cross-site request forgery or
script injection, and leaks of the SimpleFIN access URL.

## Supported versions

Only the latest release (and `main`) gets security fixes.
