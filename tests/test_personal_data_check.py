"""The personal-data guard, run against throwaway git repos in a temp folder."""
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_no_personal_data.py"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

spec = importlib.util.spec_from_file_location("check_no_personal_data", SCRIPT)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.delenv(checker.WORDS_ENV, raising=False)
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    git(r, "config", "user.email", "pat@example.com")
    git(r, "config", "user.name", "Pat Smith")
    (r / "README.md").write_text("Pat Smith pays ACME CORP.\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "init")
    return r


def add(repo, name, content="x\n", commit=False):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    git(repo, "add", "-f", name)
    if commit:
        git(repo, "commit", "-qm", f"add {name}")


def run(repo, *args):
    return checker.main(["--repo", str(repo), *args])


def test_clean_repo_passes(repo, capsys):
    assert run(repo) == 0
    assert "passed" in capsys.readouterr().out


@pytest.mark.parametrize("name", [
    "data/budget.db", "demo-data/notes.txt", "sub/data/x.txt", "budget.sqlite3", "export.CSV",
    "budget.xlsx", "old.xls", "simplefin-access-url", ".personal-words", "personal.toml",
])
def test_personal_files_fail(repo, name, capsys):
    add(repo, name)
    assert run(repo) == 1
    assert name in capsys.readouterr().out


def test_allowed_lookalikes_pass(repo):
    add(repo, "personal.example.toml")
    add(repo, "budget/data_utils.py")
    add(repo, "docs/metadata/readme.txt")
    assert run(repo) == 0


@pytest.mark.parametrize("content", [
    "url = 'https://user:" + "s3cret@bridge.simplefin.org/simplefin'\n",
    "key = AKIA" + "ABCDEFGHIJKLMNOP\n",
    "-----BEGIN RSA PRIVATE " + "KEY-----\n",
])
def test_secrets_fail(repo, content, capsys):
    add(repo, "budget/config.py", content)
    assert run(repo) == 1
    assert "looks like a secret" in capsys.readouterr().out


@pytest.mark.parametrize("content", [
    'url = f"http://user:secret@127.0.0.1:{port}/simplefin"\n',
    "url = 'https://user:pw@localhost/x'\n",
    "url = 'https://pat:pw@bank.example.com/x'\n",
    "url = 'https://pat:pw@real-bank.test/x'  # personal-data-check: ignore\n",
    "see https://bridge.simplefin.org/simplefin/create\n",
])
def test_fake_and_ignored_urls_pass(repo, content):
    add(repo, "tests/test_x.py", content)
    assert run(repo) == 0


def test_deny_list_from_untracked_file(repo, capsys):
    (repo / ".personal-words").write_text("# real names\n\nZebulon Quartermaine\n123456789\n")
    add(repo, "budget/rules.py", "MERCHANTS = {'zebulon quartermaine': 'Gifts'}\n")
    assert run(repo) == 1
    out = capsys.readouterr().out
    assert "budget/rules.py:1: matches personal deny-list entry #1" in out
    assert "Zebulon" not in out and "zebulon" not in out  # never echo the word itself


def test_deny_list_other_file_and_env(repo, tmp_path, monkeypatch):
    words = tmp_path / "words.txt"
    words.write_text("ACME CORP\n")
    assert run(repo, "--words", str(words)) == 1
    monkeypatch.setenv(checker.WORDS_ENV, str(words))
    assert run(repo) == 1


def test_missing_words_file_is_usage_error(repo, tmp_path):
    assert run(repo, "--words", str(tmp_path / "nope.txt")) == 2


def test_history_finds_removed_files(repo, capsys):
    add(repo, "data/budget.db", commit=True)
    git(repo, "rm", "-q", "--cached", "data/budget.db")
    git(repo, "commit", "-qm", "remove")
    assert run(repo) == 0
    assert run(repo, "--history") == 1
    assert "data/budget.db" in capsys.readouterr().out


def test_history_finds_removed_words(repo, tmp_path):
    add(repo, "notes.txt", "Zebulon was here\n", commit=True)
    (repo / "notes.txt").write_text("nobody\n")
    git(repo, "commit", "-qam", "scrub")
    words = tmp_path / "w.txt"
    words.write_text("zebulon\n")
    assert run(repo, "--words", str(words)) == 0
    assert run(repo, "--words", str(words), "--history") == 1


def test_this_repo_is_clean(tmp_path):
    """The real repository's tracked files pass (with an empty deny-list, not the owner's)."""
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    empty = tmp_path / "empty-words"
    empty.write_text("")
    assert checker.main(["--repo", str(ROOT), "--words", str(empty)]) == 0


def test_reviewed_history_versions_skip_only_the_secret_check(repo, tmp_path):
    """A version listed in scripts/personal-data-reviewed.txt passes the secret check in history; the
    deny-list still applies to it, and the same text in a new version still fails."""
    old = "url = 'https://u:pw@bridge/x'  # Zebulon\n"  # personal-data-check: ignore
    add(repo, "tests/t.py", old, commit=True)
    blob = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD:tests/t.py"], capture_output=True, text=True).stdout.strip()
    (repo / "tests/t.py").write_text("url = 'https://u:pw@bridge.example.com/x'\n")
    git(repo, "commit", "-qam", "fix")
    assert run(repo, "--history") == 1
    add(repo, "scripts/personal-data-reviewed.txt", f"# reviewed\n{blob} fake test URL\n", commit=True)
    assert run(repo, "--history") == 0
    words = tmp_path / "w.txt"
    words.write_text("zebulon\n")
    assert run(repo, "--words", str(words), "--history") == 1
    add(repo, "tests/t2.py", "url = 'https://u:pw@bridge/y'\n", commit=True)  # personal-data-check: ignore
    assert run(repo, "--history") == 1
