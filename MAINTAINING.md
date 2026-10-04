# Maintaining BalancePoint

Notes for the maintainer. Contributors need [CONTRIBUTING.md](CONTRIBUTING.md) instead; nothing here is
needed to use the app.

## The maintainer's own data

- The maintainer's `data/` folder is its own git repository (git-ignored by this one). `sync-mac.sh`
  commits `budget.db` there before and after each bank sync, so a bad sync is one `git revert` away.
  It skips this when `data/` isn't a git repository.
- Private notes and to-dos live in `data/TODO.md`, never in this repository.

## Publishing

`scripts/prepare_public_repo.sh` builds a public copy in a new folder and never changes this repo:

```sh
brew install git-filter-repo
scripts/prepare_public_repo.sh ~/balancepoint-public "Your Name" 12345+you@users.noreply.github.com
```

It clones `main` with `git clone --no-local`, rewrites every author and committer in that clone to the
one name and email given (`git filter-repo --mailmap`), applies text replacements from
`.public-replacements` if present (`Real Name==>Pat Smith` per line, `git filter-repo --replace-text`
format, also applied to commit messages; git ignores the file), checks the whole rewritten history
for personal data, and prints the next steps (review, create the GitHub repo, push).

**Publishing later commits:** `scripts/publish_update.sh <public-copy-folder> <remote-url> "Your Name" <email>
[--words FILE] [--replace-text FILE]` rebuilds the public copy, stops on any failed check, and pushes only
a fast-forward of what's already published (it never rewrites published history). If a check flags
made-up test values in history that's already public, list that file version's blob id in
`scripts/personal-data-reviewed.txt` after reviewing it.

Licensed under the MIT License (`LICENSE`). After creating the GitHub repository, turn on private vulnerability reporting
(Settings, then Code security) so `SECURITY.md`'s reporting route works.
