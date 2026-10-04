"""Optional notifications after a sync: off unless [notify] is set; never sent for real (fake SMTP and ntfy)."""
import os
import stat
import sys
import urllib.error
from datetime import date

import pytest

from budget import budgeting, notify, personal
from budget import simplefin_import as sf

from conftest import account, txn

SECRET = "hunter2-very-secret"


def report(added=3, stale=(), errors=(), ok=True):
    return {"start": date(2026, 3, 5), "end": date(2026, 3, 10), "unmapped": [], "missing": [], "errors": list(errors),
            "stale": [{"account": a, "last_synced": "2026-03-01"} for a in stale],
            "accounts": [{"org": "Bank", "label": "Checking ...1", "account": "Joint Checking", "added": added,
                          "read": added, "balance": 100.0, "ok": ok, "pending": 0, "settled": 0, "dropped": 0}]}


class FakeSMTP:
    sent = []

    def __init__(self, host, port, timeout=None, context=None):
        self.calls = [("connect", host, port)]
        FakeSMTP.sent.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.calls.append(("quit",))

    def starttls(self, context=None):
        self.calls.append(("starttls",))

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg, from_addr=None, to_addrs=None):
        self.calls.append(("send", from_addr, list(to_addrs)))
        self.msg = msg


class RefusingSMTP(FakeSMTP):
    def login(self, user, password):
        raise OSError(f"connection dropped while sending {password}")  # a reason that would leak it


@pytest.fixture
def fake_smtp(monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(notify.smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP.sent


@pytest.fixture
def posts(monkeypatch):
    got = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return b"{}"

    def urlopen(request, timeout=None):
        got.append(request)
        return Resp()

    monkeypatch.setattr(notify.urllib.request, "urlopen", urlopen)
    return got


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Anything not faked above fails loudly instead of reaching the internet."""
    def refuse(*a, **k):
        raise AssertionError("tried to use the network")
    monkeypatch.setattr(notify.smtplib, "SMTP", refuse)
    monkeypatch.setattr(notify.smtplib, "SMTP_SSL", refuse)
    monkeypatch.setattr(notify.urllib.request, "urlopen", refuse)
    monkeypatch.delenv(notify.PASSWORD_ENV, raising=False)


def data_dir(app):
    from pathlib import Path
    return Path(app.config["DATABASE"]).parent


def write_toml(app, text):
    (data_dir(app) / "personal.toml").write_text(text, encoding="utf-8")


EMAIL = """
[notify]
when = "{when}"
method = "email"
smtp_host = "smtp.example.com"
smtp_user = "sender@example.com"
to = ["alex@example.com", "sam@example.com"]
app_url = "http://budget-mac:5000"
"""
NTFY = """
[notify]
when = "{when}"
method = "ntfy"
ntfy_url = "https://ntfy.example.net/a-long-random-topic-name"
"""


def on_track(conn):
    """A plan with a target, on pace, no warnings."""
    chk = account(conn, "Joint Checking")
    conn.execute("INSERT INTO balances (account_id, month, amount, as_of, source) VALUES (?, '2026-03', 9000, '2026-03-01', 'manual')", (chk,))
    budgeting.set_setting(conn, "flex_target", 5500)
    txn(conn, chk, "2026-03-02", -200, "Groceries")
    txn(conn, chk, "2026-03-20", -40, None, name="Mystery Shop")  # after day 4
    conn.commit()
    return chk


def settings(app):
    return personal.load(app.config["PERSONAL_CONFIG"])


# ---------------------------------------------------------------- off by default

def run_sync(monkeypatch, capsys, app, rep=None, fail=None):
    import run

    dd = data_dir(app)
    monkeypatch.setattr(sys, "argv", ["run.py", "simplefin-sync"])
    monkeypatch.setattr(run.simplefin_import, "load_access_url", lambda d: "https://user:pw@bridge.example/simplefin")

    def fake_sync(conn, url, today, since):
        if fail:
            raise sf.SimpleFinError(fail)
        return rep or report()
    monkeypatch.setattr(run.simplefin_import, "sync", fake_sync)
    code, said = 0, ""
    try:
        run.main()
    except SystemExit as exc:
        code, said = (1 if exc.code else 0), str(exc.code or "")
    assert dd.exists()
    out = capsys.readouterr()
    out = out._replace(err=out.err + said)  # what Python prints for SystemExit("...")
    return code, out


def test_off_by_default_sends_nothing_and_prints_nothing_new(app, conn, monkeypatch, capsys):
    on_track(conn)
    budgeting.set_setting(conn, "plan_account", 1)
    budgeting.set_setting(conn, "plan_buffer", 50000)  # a warning, which would send if it were on
    conn.commit()
    code, without_file = run_sync(monkeypatch, capsys, app)
    write_toml(app, "[features]\nblackjack = false\n")
    code2, with_file = run_sync(monkeypatch, capsys, app)
    assert code == code2 == 0
    assert "Notification" not in without_file.out + without_file.err
    strip = lambda s: [line for line in s.splitlines() if not line[:4].isdigit()]  # the timestamped first line
    assert strip(without_file.out) == strip(with_file.out)
    assert notify.config(settings(app)) is None
    assert not (data_dir(app) / notify.PASSWORD_FILE).exists()


def test_off_failed_sync_unchanged(app, monkeypatch, capsys):
    code, out = run_sync(monkeypatch, capsys, app, fail="Couldn't reach SimpleFIN Bridge: timed out")
    assert code == 1 and "Notification" not in out.out + out.err


# ---------------------------------------------------------------- when

def test_warnings_mode_sends_only_with_warnings(app, conn, fake_smtp):
    write_toml(app, EMAIL.format(when="warnings"))
    on_track(conn)
    today = date(2026, 3, 4)
    s, dd = settings(app), data_dir(app)
    notify.save_password(dd, SECRET)
    lines = []
    assert not notify.after_sync(s, conn, dd, report(), budgeting.pace(conn, today), today=today, out=lines.append)
    assert fake_smtp == [] and lines == []
    assert notify.after_sync(s, conn, dd, report(stale=["Card"]), budgeting.pace(conn, today), today=today, out=lines.append)
    body = fake_smtp[-1].msg.get_content()
    assert "STALE: Card" in body and fake_smtp[-1].msg["Subject"] == "BalancePoint: 1 warning"
    # a pace warning is enough on its own
    budgeting.set_setting(conn, "flex_target", 600)
    assert notify.after_sync(s, conn, dd, report(), budgeting.pace(conn, today), today=today, out=lines.append)
    assert "ahead of pace" in fake_smtp[-1].msg.get_content()
    # and a failed sync
    assert notify.after_sync(s, conn, dd, failure="Couldn't reach https://u:pw@bridge/x", today=today, out=lines.append)
    failed = fake_smtp[-1].msg.get_content()
    assert "Bank sync FAILED" in failed and "pw" not in failed


def test_daily_always_and_message_content(app, conn, fake_smtp):
    write_toml(app, EMAIL.format(when="daily"))
    on_track(conn)
    conn.execute("UPDATE transactions SET category_source = 'mcc' WHERE name = 'Made Up'")
    conn.commit()
    today = date(2026, 3, 4)
    notify.save_password(data_dir(app), SECRET)
    assert notify.after_sync(settings(app), conn, data_dir(app), report(added=12), budgeting.pace(conn, today),
                             today=today, out=lambda line: None)
    msg = fake_smtp[0].msg
    body = msg.get_content()
    assert msg["Subject"] == "BalancePoint: daily"
    assert "Day 4 of 31: flexible $200 of $5,500, $510 under pace" in body
    assert "Bank sync: 12 new transactions." in body
    assert "To categorize: 2 (1 uncategorized, 1 guessed)." in body  # as the Categorize page counts them
    assert body.rstrip().endswith("http://budget-mac:5000")


def test_monthly_only_on_the_first_and_once(app, conn, fake_smtp):
    write_toml(app, EMAIL.format(when="monthly"))
    chk = on_track(conn)
    txn(conn, chk, "2026-02-10", -4900, "Groceries")
    txn(conn, chk, "2026-02-15", 7000, "Paycheck")
    conn.commit()
    notify.save_password(data_dir(app), SECRET)
    s, dd = settings(app), data_dir(app)
    assert not notify.after_sync(s, conn, dd, report(stale=["Card"]), today=date(2026, 3, 2), out=print)
    assert fake_smtp == []
    first = date(2026, 3, 1)
    assert notify.after_sync(s, conn, dd, report(), budgeting.pace(conn, first), today=first, out=print)
    msg = fake_smtp[0].msg
    assert msg["Subject"] == "BalancePoint: February 2026"
    body = msg.get_content()
    assert "February: flexible spending $4,900, $600 under the $5,500 target." in body
    assert "cash grew by $2,100" in body
    assert "Day 1 of 31" in body
    assert not notify.after_sync(s, conn, dd, report(), today=first, out=print)  # the Mac restarted on the 1st
    assert len(fake_smtp) == 1


# ---------------------------------------------------------------- how

def test_email_starttls_login_and_recipients(app, conn, fake_smtp):
    write_toml(app, EMAIL.format(when="daily"))
    notify.save_password(data_dir(app), SECRET)
    notify.after_sync(settings(app), conn, data_dir(app), report(), today=date(2026, 3, 4), out=print)
    calls = fake_smtp[0].calls
    assert calls[0] == ("connect", "smtp.example.com", 587)
    assert calls[1] == ("starttls",)
    assert calls[2] == ("login", "sender@example.com", SECRET)
    assert calls[3] == ("send", "sender@example.com", ["alex@example.com", "sam@example.com"])


def test_email_ssl_port_and_env_password(app, conn, fake_smtp, monkeypatch):
    write_toml(app, EMAIL.format(when="daily") + "smtp_port = 465\n")
    monkeypatch.setenv(notify.PASSWORD_ENV, "from-env")
    notify.after_sync(settings(app), conn, data_dir(app), report(), today=date(2026, 3, 4), out=print)
    calls = fake_smtp[0].calls
    assert calls[0] == ("connect", "smtp.example.com", 465) and ("starttls",) not in calls
    assert ("login", "sender@example.com", "from-env") in calls


def test_ntfy_posts_body_and_title(app, conn, posts):
    write_toml(app, NTFY.format(when="daily"))
    on_track(conn)
    today = date(2026, 3, 4)
    lines = []
    assert notify.after_sync(settings(app), conn, data_dir(app), report(), budgeting.pace(conn, today),
                             today=today, out=lines.append)
    req = posts[0]
    assert req.get_method() == "POST" and req.full_url == "https://ntfy.example.net/a-long-random-topic-name"
    assert req.get_header("Title") == "BalancePoint: daily"
    assert b"flexible $200 of $5,500" in req.data
    assert lines == ["  Notification sent: ntfy at ntfy.example.net/a-l…"]


# ---------------------------------------------------------------- failures

def test_failures_dont_change_the_exit_code_or_print_the_password(app, conn, monkeypatch, capsys):
    write_toml(app, EMAIL.format(when="daily"))
    notify.save_password(data_dir(app), SECRET)
    monkeypatch.setattr(notify.smtplib, "SMTP", RefusingSMTP)
    code, out = run_sync(monkeypatch, capsys, app)
    assert code == 0
    assert [line for line in out.out.splitlines() if "Notification" in line] == \
        ["  Notification not sent: email to smtp.example.com:587 failed: connection dropped while sending ***"]
    assert SECRET not in out.out + out.err
    # a failed sync still fails the same way, with the message attempted first
    code, out = run_sync(monkeypatch, capsys, app, fail="Couldn't reach SimpleFIN Bridge: timed out")
    assert code == 1 and "Notification not sent" in out.out and SECRET not in out.out + out.err
    assert "SimpleFIN sync FAILED" in out.err


def test_missing_password_and_bad_ntfy_are_one_line(app, conn, monkeypatch, capsys, posts):
    write_toml(app, EMAIL.format(when="daily"))
    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    code, out = run_sync(monkeypatch, capsys, app)
    assert code == 0 and "  Notification not sent: no SMTP password: run `run.py notify-setup-password`" in out.out

    def refused(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)
    monkeypatch.setattr(notify.urllib.request, "urlopen", refused)
    write_toml(app, NTFY.format(when="daily"))
    code, out = run_sync(monkeypatch, capsys, app)
    assert code == 0 and "  Notification not sent: ntfy server ntfy.example.net answered 403 Forbidden" in out.out
    assert "a-long-random-topic" not in out.out


def test_bad_config_is_one_line_in_sync_and_friendly(app, conn, monkeypatch, capsys):
    write_toml(app, '[notify]\nmethod = "pigeon"\n')
    code, out = run_sync(monkeypatch, capsys, app)
    assert code == 0
    assert [line for line in out.out.splitlines() if "Notification" in line][0].startswith(
        "  Notification not sent: ") and "pigeon" in out.out


@pytest.mark.parametrize("toml, says", [
    ('method = "pigeon"', 'method = "email" or "ntfy"'),
    ('method = "email"\nwhen = "hourly"', '"warnings", "daily" or "monthly"'),
    ('method = "email"\nsmtp_host = "smtp.example.com"', "needs to ="),
    ('method = "email"\nto = ["a@example.com"]', "needs smtp_host"),
    ('method = "email"\nto = "a@example.com"\nsmtp_host = "h"\nsmtp_port = "587"', "smtp_port"),
    ('method = "email"\nto = "a@example.com"\nsmtp_host = "h"', "needs from"),
    ('method = "ntfy"\nntfy_url = "ntfy.sh/topic"', "needs ntfy_url"),
    ('method = "ntfy"\nntfy_url = "https://ntfy.sh/t"\npassword = "x"', "doesn't know: password"),
])
def test_config_errors_are_friendly(tmp_path, toml, says):
    path = tmp_path / "personal.toml"
    path.write_text("[notify]\n" + toml + "\n")
    with pytest.raises(personal.PersonalConfigError, match=None) as exc:
        notify.config(personal.load(path))
    assert says in str(exc.value) and str(path) in str(exc.value)


def test_to_as_one_address_and_describe_masks(tmp_path):
    path = tmp_path / "personal.toml"
    path.write_text('[notify]\nmethod = "email"\nto = "alex@example.com"\nsmtp_host = "h"\nfrom = "me@example.com"\n')
    cfg = notify.config(personal.load(path))
    assert cfg["to"] == ["alex@example.com"] and cfg["when"] == "warnings" and cfg["smtp_port"] == 587
    assert notify.describe(cfg) == "email to a…@example.com, when there are warnings"


# ---------------------------------------------------------------- the password file and the commands

def test_password_file_is_private_and_git_ignored(tmp_path):
    (tmp_path / ".gitignore").write_text("simplefin-access-url\n")
    path = notify.save_password(tmp_path, SECRET)
    notify.save_password(tmp_path, SECRET)  # again: no duplicate line
    assert notify.load_password(tmp_path) == SECRET
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert (tmp_path / ".gitignore").read_text().splitlines() == ["simplefin-access-url", notify.PASSWORD_FILE]


def test_setup_password_command_never_echoes(app, monkeypatch, capsys):
    import getpass

    import run

    monkeypatch.setattr(sys, "argv", ["run.py", "notify-setup-password"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": SECRET)
    run.main()
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err
    assert notify.load_password(data_dir(app)) == SECRET


def test_notify_test_command(app, conn, monkeypatch, capsys, posts):
    import run

    monkeypatch.setattr(sys, "argv", ["run.py", "notify-test"])
    with pytest.raises(SystemExit, match="aren't set up"):
        run.main()
    write_toml(app, NTFY.format(when="warnings"))  # sends even with no warnings: it's a test
    run.main()
    assert posts[0].get_header("Title") == "BalancePoint: test message"
    assert "Sent a test message" in capsys.readouterr().out


def test_bank_sync_page_line_only_when_set_up(app, client):
    assert "Notifications after" not in client.get("/bank-sync").get_data(as_text=True)
    write_toml(app, EMAIL.format(when="warnings"))
    page = client.get("/bank-sync").get_data(as_text=True)
    assert "email to a…@example.com, s…@example.com, when there are warnings." in page
    assert "alex@" not in page
