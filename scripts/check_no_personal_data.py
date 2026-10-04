#!/usr/bin/env python3
"""Fail if git-tracked files could hold personal data or secrets.

Checks, for the files git tracks (or, with --history, every file ever committed):

1. Paths: data/, demo-data/, databases, spreadsheets, CSVs and simplefin-access-url.
2. Secrets in text: URLs with a password in them (like a SimpleFIN access URL),
   AWS-style access keys and private-key blocks. Local test servers (localhost,
   127.0.0.1, example.com) are allowed.
3. Your own deny-list: words read from an untracked local file (default
   .personal-words at the repo root, which git ignores). One entry per line, matched
   case-insensitively anywhere in tracked text; blank lines and lines starting with #
   are skipped. Put real names, street names, account numbers, employers there.
   Matches are reported by file, line and entry number, never by the word itself, so
   the output is safe to paste or to show in CI logs.

A line containing "personal-data-check: ignore" is skipped for checks 2 and 3.
With --history, file versions listed in scripts/personal-data-reviewed.txt (full blob id, then why) were
reviewed and their "secret" matches are made-up test values: check 2 is skipped for exactly those
versions (the deny-list still applies). Use it only for history that can no longer be rewritten.

Exit status 0 when clean, 1 when something was found, 2 on a usage error.

    python scripts/check_no_personal_data.py
    python scripts/check_no_personal_data.py --words ~/my-words.txt
    python scripts/check_no_personal_data.py --history      # every commit, not just HEAD
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_WORDS_FILE = ".personal-words"
WORDS_ENV = "PERSONAL_WORDS_FILE"
IGNORE_MARKER = "personal-data-check: ignore"

FORBIDDEN_PATHS = [
    (re.compile(r"(^|/)data/"), "data/ folder"),
    (re.compile(r"(^|/)demo-data/"), "demo-data/ folder"),
    (re.compile(r"\.(db|sqlite3?|db-journal|db-wal|db-shm)$", re.I), "database file"),
    (re.compile(r"\.(xlsx|xlsm|xls)$", re.I), "spreadsheet"),
    (re.compile(r"\.csv$", re.I), "CSV file"),
    (re.compile(r"simplefin-access-url"), "SimpleFIN access URL file"),
    (re.compile(r"(^|/)\.personal-words$"), "personal deny-list"),
    (re.compile(r"(^|/)personal\.toml$"), "personal settings (only personal.example.toml belongs in git)"),
]

_SAFE_HOSTS = r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]|(?:[\w-]+\.)*example\.(?:com|org|net))"
SECRET_PATTERNS = [
    # a URL carrying "user:password" before an "@host", unless the host is a local or example one
    (re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@'\"]+:[^\s/@'\"]+@(?!" + _SAFE_HOSTS + r"[:/'\"\s]|"
                + _SAFE_HOSTS + r"$)", re.I), "URL with a password"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "AWS access key id"),
    (re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE " + r"KEY-----"), "private key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "GitHub token"),
]


def git(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


def repo_root(start: Path) -> Path:
    return Path(git(start, "rev-parse", "--show-toplevel").decode().strip())


def load_words(path: Path | None) -> list[str]:
    if path is None or not path.is_file():
        return []
    words = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            words.append(line)
    return words


def check_paths(paths: list[str]) -> list[str]:
    problems = []
    for p in paths:
        for pattern, why in FORBIDDEN_PATHS:
            if pattern.search(p):
                problems.append(f"{p}: {why} must not be in git")
                break
    return problems


def check_text(name: str, data: bytes, words: list[str], secrets: bool = True) -> list[str]:
    if b"\0" in data[:8192]:
        return []  # binary
    text = data.decode("utf-8", errors="replace")
    lowered = [w.lower() for w in words]
    problems = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if IGNORE_MARKER in line:
            continue
        for pattern, why in SECRET_PATTERNS if secrets else ():
            if pattern.search(line):
                problems.append(f"{name}:{lineno}: looks like a secret ({why})")
        low = line.lower()
        for i, w in enumerate(lowered, 1):
            if w in low:
                problems.append(f"{name}:{lineno}: matches personal deny-list entry #{i}")
    return problems


def scan_head(repo: Path, words: list[str]) -> list[str]:
    paths = [p for p in git(repo, "ls-files", "-z").decode().split("\0") if p]
    problems = check_paths(paths)
    for p in paths:
        f = repo / p
        if f.is_file() and not f.is_symlink():
            problems += check_text(p, f.read_bytes(), words)
    return problems


def reviewed_blobs(repo: Path) -> set[str]:
    """Full blob ids from scripts/personal-data-reviewed.txt (as committed at HEAD)."""
    try:
        text = git(repo, "show", "HEAD:scripts/personal-data-reviewed.txt").decode()
    except subprocess.CalledProcessError:
        return set()
    return {line.split()[0] for line in text.splitlines()
            if line.strip() and not line.startswith("#") and len(line.split()[0]) == 40}


def scan_history(repo: Path, words: list[str]) -> list[str]:
    """Every path and every blob ever committed on any ref."""
    out = git(repo, "log", "--all", "--format=", "--name-only", "-z").decode()
    paths = sorted({p.strip("\n") for p in out.split("\0") if p.strip("\n")})
    problems = check_paths(paths)
    # Each distinct blob once, with one path it was seen at.
    seen: dict[str, str] = {}
    listing = git(repo, "rev-list", "--all", "--objects").decode().splitlines()
    for row in listing:
        sha, _, path = row.partition(" ")
        if path and sha not in seen:
            seen[sha] = path
    if not seen:
        return problems
    proc = subprocess.run(["git", "-C", str(repo), "cat-file", "--batch-check=%(objectname) %(objecttype)"],
                          input="\n".join(seen).encode() + b"\n", capture_output=True, check=True)
    blobs = [line.split()[0] for line in proc.stdout.decode().splitlines() if line.endswith(" blob")]
    reviewed = reviewed_blobs(repo)
    for sha in blobs:
        data = git(repo, "cat-file", "blob", sha)
        for problem in check_text(seen[sha], data, words, secrets=sha not in reviewed):
            problems.append(f"{problem} (blob {sha[:10]})")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, default=Path.cwd(), help="repository to check (default: here)")
    ap.add_argument("--words", type=Path, default=None,
                    help=f"deny-list file (default: ${WORDS_ENV}, else {DEFAULT_WORDS_FILE} at the repo root)")
    ap.add_argument("--history", action="store_true", help="check every commit on every ref, not just HEAD")
    args = ap.parse_args(argv)

    try:
        root = repo_root(args.repo)
    except subprocess.CalledProcessError:
        print(f"{args.repo} is not a git repository", file=sys.stderr)
        return 2

    words_file = args.words or (Path(os.environ[WORDS_ENV]) if os.environ.get(WORDS_ENV) else root / DEFAULT_WORDS_FILE)
    if args.words and not args.words.is_file():
        print(f"Deny-list file not found: {args.words}", file=sys.stderr)
        return 2
    words = load_words(words_file)

    problems = scan_history(root, words) if args.history else scan_head(root, words)
    scope = "history" if args.history else "tracked files"
    note = f", {len(words)} deny-list entries" if words else ", no deny-list entries"
    if problems:
        for p in problems:
            print(p)
        print(f"\nPersonal data check FAILED ({scope}{note}): {len(problems)} problem(s).")
        return 1
    print(f"Personal data check passed ({scope}{note}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
