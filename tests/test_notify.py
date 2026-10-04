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
    monkeypatch.setattr(run.simplefin_import, "load_access_url", lambda d: "https://user:pw@bridge.example.com/simplefin")

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
    assert notify.after_sync(s, conn, dd, failure="Couldn't reach https://u:pw@bridge.example.com/x", today=today, out=lines.append)
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
    ('method = "email"\nwhen = "hourly"', '"warnings", "daily", "weekly" or "monthly"'),
    ('method = "email"\nwhen = "weekly"\nweekday = "someday"', 'use a day name'),
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


# ---------------------------------------------------------------- weekly

def week_of_spending(conn):
    chk = on_track(conn)  # Groceries $200 on Mar 2
    for day, amount, cat in [("2026-03-03", -50, "Dining & Drinks"), ("2026-03-04", -120, "Bills & Utilities"),
                             ("2026-03-05", -300, "Travel"), ("2026-03-06", -40, "Auto & Gas"),
                             ("2026-03-07", -10, "Pets"), ("2026-03-09", -999, "Groceries")]:  # Mar 9: not last week
        txn(conn, chk, day, amount, cat)
    conn.commit()


def test_weekly_only_on_the_weekday_and_once_a_week(app, conn, fake_smtp, monkeypatch):
    from budget import recurring

    write_toml(app, EMAIL.format(when="weekly"))  # weekday: monday by default
    week_of_spending(conn)
    monkeypatch.setattr(recurring, "summary", lambda conn, today: {
        "new_items": [{"name": "Streamflix", "typical": 15.49, "previous": None, "cadence": "monthly"}],
        "price_up_items": [{"name": "Gym", "typical": 45.0, "previous": 40.0, "cadence": "monthly"}]})
    notify.save_password(data_dir(app), SECRET)
    s, dd = settings(app), data_dir(app)
    sunday, monday = date(2026, 3, 8), date(2026, 3, 9)
    assert not notify.after_sync(s, conn, dd, report(stale=["Card"]), today=sunday, out=print)
    assert fake_smtp == []
    lines = []
    assert notify.after_sync(s, conn, dd, report(), budgeting.pace(conn, monday), today=monday, out=lines.append)
    msg = fake_smtp[0].msg
    assert msg["Subject"] == "BalancePoint: week of Mar 2 – 8"
    body = msg.get_content()
    assert "Last 7 days (Mar 2 – 8): spent $720: flexible $300, fixed $120, non-monthly $300." in body
    assert "Top flexible: Groceries $200, Dining & Drinks $50, Auto & Gas $40." in body
    assert "Day 9 of 31" in body
    assert "New regular charge: Streamflix, $15.49 monthly" in body
    assert "Price went up: Gym, $40.00 to $45.00" in body
    assert "Bank sync: 3 new transactions." in body
    assert lines == ["  Notification sent: email to a…@example.com, s…@example.com"]
    assert budgeting.get_setting(conn, notify.WEEKLY_SENT_KEY, cast=str) == "2026-W11"
    assert not notify.after_sync(s, conn, dd, report(), today=monday, out=print)  # the Mac restarted
    assert len(fake_smtp) == 1
    assert notify.after_sync(s, conn, dd, report(), today=date(2026, 3, 16), out=print)  # next Monday
    assert len(fake_smtp) == 2


def test_weekly_weekday_names_and_recurring_trouble(app, conn, fake_smtp, monkeypatch):
    from budget import recurring

    write_toml(app, EMAIL.format(when="weekly") + 'weekday = "Wednesdays"\n')
    cfg = notify.config(settings(app))
    assert cfg["weekday"] == "wednesday"
    assert notify.describe(cfg) == "email to a…@example.com, s…@example.com, a weekly recap on Wednesdays"
    for name in ("wed", "WED", "Wednesday", "wednesday"):
        assert notify.parse_weekday(name) == "wednesday"
    assert notify.parse_weekday("we") is None and notify.parse_weekday(3) is None

    def broken(conn, today):
        raise RuntimeError("no")
    monkeypatch.setattr(recurring, "summary", broken)
    week_of_spending(conn)
    notify.save_password(data_dir(app), SECRET)
    assert not notify.after_sync(settings(app), conn, data_dir(app), report(), today=date(2026, 3, 9), out=print)
    assert notify.after_sync(settings(app), conn, data_dir(app), report(), today=date(2026, 3, 11), out=print)
    assert "Last 7 days (Mar 4 – 10)" in fake_smtp[0].msg.get_content()


# ---------------------------------------------------------------- set up from the web

WEB = {"method": "email", "when": "daily", "weekday": "monday", "smtp_host": "smtp.gmail.com", "smtp_port": "587",
       "smtp_user": "owner@gmail.com", "from": "", "to": "owner@gmail.com, partner@example.com",
       "ntfy_url": "", "app_url": "http://budget-mac:5000", "password": SECRET, "action": "save"}


def save_web(client, **changes):
    return client.post("/notifications", data={**WEB, **changes}, follow_redirects=True)


def test_web_page_defaults_and_bank_sync_link(client):
    page = client.get("/notifications").get_data(as_text=True)
    assert 'value="smtp.gmail.com"' in page and 'value="587"' in page and 'value="http://localhost"' in page
    assert "Gmail app password" in page and "Nothing is sent." in page
    assert 'autocomplete="new-password"' in page
    assert "set up notifications" in client.get("/bank-sync").get_data(as_text=True)


def test_web_save_and_reload_never_shows_the_password(app, conn, client):
    resp = save_web(client)
    page = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "Saved. Messages go by email to o…@gmail.com, p…@example.com, after every sync." in page
    reload = client.get("/notifications").get_data(as_text=True)
    for html in (page, reload):
        assert SECRET not in html
        assert 'name="password" type="password" value=""' in html
        assert "One is saved (never shown)" in html
    assert 'value="owner@gmail.com, partner@example.com"' in reload and 'value="http://budget-mac:5000"' in reload
    assert 'option value="daily" selected' in reload
    cfg = notify.config(settings(app), conn)
    assert cfg["source"] == "web" and cfg["to"] == ["owner@gmail.com", "partner@example.com"]
    assert cfg["sender"] == "owner@gmail.com" and cfg["smtp_port"] == 587
    path = notify.password_path(data_dir(app))
    assert notify.load_password(data_dir(app)) == SECRET
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert notify.PASSWORD_FILE in (data_dir(app) / ".gitignore").read_text().splitlines()
    assert SECRET not in budgeting.get_setting(conn, notify.WEB_KEY, cast=str)
    bank = client.get("/bank-sync").get_data(as_text=True)
    assert "Notifications after the scheduled sync: email to o…@gmail.com" in bank and SECRET not in bank


def test_web_blank_password_keeps_new_replaces_remove_removes(app, client):
    save_web(client)
    save_web(client, password="", when="warnings")
    assert notify.load_password(data_dir(app)) == SECRET
    save_web(client, password="abcd efgh ijkl mnop")  # an app password as Google shows it
    assert notify.load_password(data_dir(app)) == "abcdefghijklmnop"
    page = save_web(client, password="", remove_password="1").get_data(as_text=True)
    assert notify.load_password(data_dir(app)) is None and "None saved yet." in page


def test_web_bad_settings_are_not_saved_and_say_why(app, conn, client):
    resp = save_web(client, to="")
    page = resp.get_data(as_text=True)
    assert resp.status_code == 400 and "Notification settings needs to =" in page
    assert SECRET not in page and 'value="smtp.gmail.com"' in page
    assert budgeting.get_setting(conn, notify.WEB_KEY) is None
    assert notify.load_password(data_dir(app)) is None


def test_web_weekly_and_ntfy_login_never_shown(app, conn, client, posts):
    ntfy = {"method": "ntfy", "when": "weekly", "weekday": "friday", "password": ""}
    save_web(client, **ntfy, ntfy_url="https://bob:topsecret@ntfy.example.net/a-long-random-topic-name")
    page = client.get("/notifications").get_data(as_text=True)
    assert "topsecret" not in page and "bob" not in page
    assert 'value="https://ntfy.example.net/a-long-random-topic-name"' in page and "has a login in it" in page
    # saved again as the page shows it: the login stays
    save_web(client, **ntfy, ntfy_url="https://ntfy.example.net/a-long-random-topic-name")
    cfg = notify.config(settings(app), conn)
    assert cfg["weekday"] == "friday"
    assert cfg["ntfy_url"] == "https://bob:topsecret@ntfy.example.net/a-long-random-topic-name"
    page = save_web(client, **ntfy, ntfy_url="https://ntfy.example.net/a-long-random-topic-name",
                    action="test").get_data(as_text=True)
    assert "Sent a test message (ntfy at ntfy.example.net/a-l…)" in page and "topsecret" not in page
    req = posts[0]
    assert req.full_url == "https://ntfy.example.net/a-long-random-topic-name"
    assert req.get_header("Authorization") == "Basic Ym9iOnRvcHNlY3JldA=="  # bob:topsecret
    assert "Last 7 days" in req.data.decode()  # a weekly setup's test shows the recap


def test_web_test_message_by_smtp(app, client, fake_smtp, monkeypatch):
    page = save_web(client, action="test").get_data(as_text=True)
    assert "Sent a test message (email to o…@gmail.com, p…@example.com)" in page and SECRET not in page
    calls = fake_smtp[0].calls
    assert calls[0] == ("connect", "smtp.gmail.com", 587) and ("login", "owner@gmail.com", SECRET) in calls
    assert fake_smtp[0].msg["Subject"] == "BalancePoint: test message"
    monkeypatch.setattr(notify.smtplib, "SMTP", RefusingSMTP)
    page = save_web(client, action="test", password="").get_data(as_text=True)
    assert "Test message not sent: email to smtp.gmail.com:587 failed: connection dropped while sending ***" in page
    assert SECRET not in page


def test_personal_toml_wins_over_the_page(app, conn, client, posts):
    save_web(client)
    write_toml(app, NTFY.format(when="daily"))
    cfg = notify.config(settings(app), conn)
    assert cfg["method"] == "ntfy" and cfg["source"] == "personal.toml"
    page = client.get("/notifications").get_data(as_text=True)
    assert "Set in personal.toml" in page and 'name="smtp_host"' not in page and 'name="password"' not in page
    assert 'value="off"' not in page
    client.post("/notifications", data={"action": "off"})
    assert notify.config(settings(app), conn)["method"] == "ntfy"
    assert notify.after_sync(settings(app), conn, data_dir(app), report(), today=date(2026, 3, 4), out=print)
    assert len(posts) == 1


def test_web_setup_sends_after_the_sync_and_notify_test(app, client, monkeypatch, capsys, fake_smtp):
    import run

    save_web(client)
    code, out = run_sync(monkeypatch, capsys, app)
    assert code == 0 and "  Notification sent: email to o…@gmail.com, p…@example.com" in out.out
    assert SECRET not in out.out + out.err and len(fake_smtp) == 1
    monkeypatch.setattr(sys, "argv", ["run.py", "notify-test"])
    run.main()
    assert "Sent a test message" in capsys.readouterr().out and len(fake_smtp) == 2


def test_turned_off_sends_nothing_and_sync_output_is_unchanged(app, conn, client, monkeypatch, capsys):
    on_track(conn)
    budgeting.set_setting(conn, "plan_account", 1)
    budgeting.set_setting(conn, "plan_buffer", 50000)  # a warning, which would send if it were on
    conn.commit()
    _, never = run_sync(monkeypatch, capsys, app)
    save_web(client, when="warnings")
    page = client.post("/notifications", data={"action": "off"}, follow_redirects=True).get_data(as_text=True)
    assert "Notifications are off" in page and "Nothing is sent." in page
    assert 'value="owner@gmail.com, partner@example.com"' in page  # kept for next time
    assert notify.config(settings(app), conn) is None
    _, off = run_sync(monkeypatch, capsys, app)  # no_network fails the test on any attempt to send
    strip = lambda s: [line for line in s.splitlines() if not line[:4].isdigit()]  # noqa: E731
    assert "Notification" not in off.out + off.err and strip(never.out) == strip(off.out)
    assert "Notifications after" not in client.get("/bank-sync").get_data(as_text=True)
