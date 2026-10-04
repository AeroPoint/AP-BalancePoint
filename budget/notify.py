"""Optional: a short message after the scheduled bank sync (README: "Notifications (optional)").

Off unless data/personal.toml has a [notify] section or they were saved on the Notifications page (the
settings table, WEB_KEY; personal.toml wins when it has a [notify] section). run.py simplefin-sync calls
after_sync() once it has printed its summary; with neither, that returns at once, prints nothing and sends
nothing. A message that can't be sent is one "Notification not sent: <reason>" line in the log, never a
failed sync.

Two ways to send:
  * email over SMTP: the password is never in personal.toml, but in data/notify-password (only your
    user can read it; run.py notify-setup-password or the Notifications page writes it) or the
    BUDGET_SMTP_PASSWORD variable. It is never shown, logged or sent anywhere but the mail server.
  * ntfy (ntfy.sh or your own server): a POST to ntfy_url, which shows up as a phone notification. A
    user:password in the link is sent as a login and never shown.
"""
import base64
import calendar
import json
import os
import re
import smtplib
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

from . import budgeting
from .personal import PersonalConfigError

PASSWORD_FILE = "notify-password"   # in the data folder, next to simplefin-access-url
PASSWORD_ENV = "BUDGET_SMTP_PASSWORD"
WHEN = ("warnings", "daily", "weekly", "monthly")
WEEKDAYS = tuple(calendar.day_name[i].lower() for i in range(7))  # monday .. sunday, date.weekday() order
METHODS = ("email", "ntfy")
KEYS = {"when", "weekday", "method", "app_url", "smtp_host", "smtp_port", "smtp_user", "to", "from", "ntfy_url"}
MONTHLY_SENT_KEY = "notify_monthly_sent"  # settings: the month whose monthly message went out
WEEKLY_SENT_KEY = "notify_weekly_sent"    # settings: the ISO week ("2026-W40") whose weekly message went out
WEB_KEY = "notify_config"                 # settings: what the Notifications page saved, as JSON (never a password)
WEB_WHERE = "Notification settings"
TIMEOUT = 30


class NotifyError(RuntimeError):
    pass


# ---------------------------------------------------------------- settings

def config(settings, conn=None):
    """The checked settings in effect, or None when notifications are off.

    personal.toml's [notify] section wins; without one, what the Notifications page saved (when a database
    connection is given). cfg["source"] says which: "personal.toml" or "web".
    """
    if settings.notify:
        return check(settings.notify, f"{settings.path}: [notify]", "personal.toml")
    raw = web_settings(conn) if conn is not None else None
    if not raw or not raw.get("enabled"):
        return None
    return check({k: v for k, v in raw.items() if k != "enabled"}, WEB_WHERE, "web")


def web_settings(conn):
    """What the Notifications page saved (a dict, "enabled" says whether it's on), or None."""
    text = budgeting.get_setting(conn, WEB_KEY, cast=str)
    if not text:
        return None
    try:
        raw = json.loads(text)
    except ValueError:
        raw = None
    if not isinstance(raw, dict):
        raise PersonalConfigError(f"{WEB_WHERE} can't be read: save them again on the Notifications page.")
    return raw


def save_web_settings(conn, raw):
    budgeting.set_setting(conn, WEB_KEY, None if raw is None else json.dumps(raw))


def parse_weekday(value):
    """'monday', 'Mon', 'MONDAYS' -> 'monday'; None when it isn't a day."""
    if not isinstance(value, str):
        return None
    v = value.strip().lower().removesuffix(".")
    v = v[:-1] if v.endswith("days") else v  # "mondays"
    return next((d for d in WEEKDAYS if len(v) >= 3 and d.startswith(v)), None)


def week_key(day):
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def check(raw, where, source="personal.toml"):
    """Checked settings from a raw [notify] table (or the page's saved settings, same keys)."""
    if not isinstance(raw, dict):
        raise PersonalConfigError(f"{where} should be a section of settings, like personal.example.toml shows.")
    unknown = sorted(set(raw) - KEYS)
    if unknown:
        raise PersonalConfigError(f"{where} has a setting it doesn't know: {', '.join(unknown)}. "
                                  f"Known: {', '.join(sorted(KEYS))}.")
    cfg = {"when": raw.get("when", "warnings"), "method": raw.get("method"), "app_url": raw.get("app_url") or None,
           "source": source}
    if cfg["when"] not in WHEN:
        raise PersonalConfigError(f"{where} when = {cfg['when']!r}; "
                                  "use \"warnings\", \"daily\", \"weekly\" or \"monthly\".")
    if cfg["when"] == "weekly":
        cfg["weekday"] = parse_weekday(raw.get("weekday", "monday"))
        if cfg["weekday"] is None:
            raise PersonalConfigError(f"{where} weekday = {raw.get('weekday')!r}; use a day name, like \"monday\".")
    if cfg["method"] not in METHODS:
        raise PersonalConfigError(f"{where} needs method = \"email\" or \"ntfy\" (it has {cfg['method']!r}).")
    if cfg["method"] == "email":
        to = raw.get("to")
        to = [to] if isinstance(to, str) else to
        if not to or not all(isinstance(a, str) and "@" in a for a in to):
            raise PersonalConfigError(f'{where} needs to = ["you@example.com"], the address(es) to send to.')
        if not isinstance(raw.get("smtp_host"), str) or not raw["smtp_host"].strip():
            raise PersonalConfigError(f'{where} needs smtp_host, your mail provider\'s server, like "smtp.gmail.com".')
        port = raw.get("smtp_port", 587)
        if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
            raise PersonalConfigError(f"{where} smtp_port = {port!r}; use a number: 587 (STARTTLS) or 465 (SSL).")
        user = raw.get("smtp_user")
        if user is not None and not isinstance(user, str):
            raise PersonalConfigError(f"{where} smtp_user should be text, like \"you@example.com\".")
        sender = raw.get("from") or user
        if not isinstance(sender, str) or "@" not in sender:
            raise PersonalConfigError(f'{where} needs from = "you@example.com" (or smtp_user as an email address).')
        cfg.update(smtp_host=raw["smtp_host"].strip(), smtp_port=port, smtp_user=user or None, to=to, sender=sender)
    else:
        url = raw.get("ntfy_url")
        parts = urllib.parse.urlsplit(url) if isinstance(url, str) else None
        if not parts or parts.scheme not in ("http", "https") or not parts.netloc or len(parts.path.strip("/")) < 1:
            raise PersonalConfigError(f'{where} needs ntfy_url = "https://ntfy.sh/<a long random topic>" '
                                      "(or your own ntfy server).")
        cfg["ntfy_url"] = url
    if cfg["app_url"] is not None and not isinstance(cfg["app_url"], str):
        raise PersonalConfigError(f"{where} app_url should be a link, like \"http://budget-mac:5000\".")
    return cfg


def describe(cfg):
    """One line for people to read, with addresses masked: 'email to a…@example.com, when there are warnings'."""
    if cfg["method"] == "email":
        how = "email to " + ", ".join(mask_address(a) for a in cfg["to"])
    else:
        parts = urllib.parse.urlsplit(cfg["ntfy_url"])
        how = f"ntfy at {parts.hostname}/{parts.path.strip('/')[:3]}…"
    when = {"warnings": "when there are warnings", "daily": "after every sync",
            "weekly": f"a weekly recap on {cfg.get('weekday', 'monday').capitalize()}s",
            "monthly": "on the 1st of each month"}[cfg["when"]]
    return f"{how}, {when}"


def mask_address(address):
    name, _, domain = address.strip().partition("@")
    return f"{name[:1]}…@{domain}"


# ---------------------------------------------------------------- the SMTP password (a credential)

def password_path(data_dir):
    return Path(data_dir) / PASSWORD_FILE


def save_password(data_dir, password):
    """Write the SMTP password readable only by this user, and keep it out of data/'s own git repo."""
    path = password_path(data_dir)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(password + "\n")
    path.chmod(0o600)
    ignore = Path(data_dir) / ".gitignore"
    lines = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
    if PASSWORD_FILE not in lines:
        ignore.write_text("\n".join(lines + [PASSWORD_FILE]) + "\n", encoding="utf-8")
    return path


def load_password(data_dir):
    if os.environ.get(PASSWORD_ENV):
        return os.environ[PASSWORD_ENV]
    path = password_path(data_dir)
    return path.read_text(encoding="utf-8").rstrip("\r\n") if path.exists() else None


# ---------------------------------------------------------------- the message

def _money(x):
    return f"${abs(x):,.0f}"


def to_categorize(conn):
    """What the Categorize page lists: uncategorized, and guessed from the card's merchant code."""
    uncategorized = conn.execute("SELECT COUNT(*) FROM transactions WHERE category_id IS NULL").fetchone()[0]
    guessed = conn.execute("SELECT COUNT(*) FROM transactions WHERE category_source = 'mcc'").fetchone()[0]
    return uncategorized, guessed


def sync_problems(report, failure=None):
    """Lines about a sync that went wrong somewhere: the whole sync, a bank, a stale account."""
    if failure:
        return [f"Bank sync FAILED, nothing changed: {re.sub(r'://[^/\s@]+@', '://', failure)}"]
    if report is None:
        return []
    out = [f"{a['account']}: the bank reported a problem (will retry)" for a in report["accounts"] if not a["ok"]]
    out += [f"Bridge says: {e.get('msg') if isinstance(e, dict) else e}" for e in report["errors"]]
    out += [f"{name}: mapped, but the Bridge didn't return it" for name in report["missing"]]
    out += [f"STALE: {s['account']} last pulled {s['last_synced'] or 'never'}" for s in report["stale"]]
    return out


def pace_line(check):
    if not check or check.get("spent") is None:
        return None
    ahead = check["ahead"]
    return (f"Day {check['day']} of {check['days']}: flexible {_money(check['spent'])} of {_money(check['target'])}, "
            f"{_money(ahead)} {'over' if ahead > 0 else 'under'} pace")


def month_summary(conn, today):
    """Last month's result, as the Overview statement puts it."""
    y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    card = budgeting.scorecard(conn, y, m)
    name = calendar.month_name[m]
    line = f"{name}: flexible spending {_money(card['flexible'])}"
    if card["target"]:
        d = card["flexible"] - card["target"]
        line += f", {_money(d)} {'over' if d > 0 else 'under'} the {_money(card['target'])} target"
    lines = [line + "."]
    lines.append(f"Brought in {_money(card['income'])}, spent {_money(card['spending'])}: cash "
                 f"{'grew' if card['cash_flow'] >= 0 else 'shrank'} by {_money(card['cash_flow'])}.")
    if card["work"]["total"] and card["rate"] is not None:
        lines.append(f"Counting retirement from paychecks, you saved {_money(card['saved'])}, "
                     f"{round(card['rate'] * 100)}% of income.")
    return f"{name} {y}", lines


def _cents(x):
    return f"${abs(x):,.2f}"


def week_summary(conn, today):
    """The last 7 days (through yesterday) by group, and the top 3 flexible categories."""
    start, last = today - timedelta(days=7), today - timedelta(days=1)
    groups, flexible = budgeting.by_group(conn, start.isoformat(), today.isoformat())
    label = f"{start:%b} {start.day}" + (f" – {last.day}" if start.month == last.month else f" – {last:%b} {last.day}")
    total = sum(groups.values())
    line = (f"Last 7 days ({label}): spent {_money(total)}: flexible {_money(groups['flexible'])}, "
            f"fixed {_money(groups['fixed'])}, non-monthly {_money(groups['nonmonthly'])}")
    if groups["one_off"]:
        line += f", one-offs {_money(groups['one_off'])}"
    lines = [line + "."]
    top = sorted(((v, name) for (_, name), v in flexible.items() if v > 0), key=lambda t: (-t[0], t[1]))[:3]
    if top:
        lines.append("Top flexible: " + ", ".join(f"{name} {_money(v)}" for v, name in top) + ".")
    return label, lines


def recurring_lines(conn, today):
    """New regular charges and price increases from the Recurring page; nothing if that can't be worked out."""
    try:
        from . import recurring

        s = recurring.summary(conn, today)
        lines = [f"New regular charge: {i['name']}, {_cents(i['typical'])} {i.get('cadence') or ''}".rstrip()
                 for i in s.get("new_items") or []]
        lines += [f"Price went up: {i['name']}, {_cents(i['previous'])} to {_cents(i['typical'])}"
                  if i.get("previous") is not None else f"Price went up: {i['name']}, now {_cents(i['typical'])}"
                  for i in s.get("price_up_items") or []]
        return lines
    except Exception:  # noqa: BLE001  a recap without this part beats no recap
        return []


def build(conn, cfg, today, report=None, check=None, failure=None, test=False):
    """(title, body) for this run, or None when `when` says this run sends nothing."""
    warnings = list((check or {}).get("warnings", []))
    problems = sync_problems(report, failure)
    monthly = cfg["when"] == "monthly" and today.day == 1
    weekly = cfg["when"] == "weekly" and (test or WEEKDAYS[today.weekday()] == cfg["weekday"])
    if not test:
        if cfg["when"] == "warnings" and not (warnings or problems):
            return None
        if cfg["when"] == "monthly" and not monthly:
            return None
        if cfg["when"] == "weekly" and not weekly:
            return None
    lines = []
    if test:
        lines += ["Notifications work. This is what a message looks like with today's numbers.", ""]
    if monthly:
        label, summary = month_summary(conn, today)
        title = f"BalancePoint: {label}"
        lines += summary + [""]
    elif weekly:
        label, summary = week_summary(conn, today)
        title = f"BalancePoint: week of {label}"
        lines += summary + [""]
    if test:
        title = "BalancePoint: test message"
    elif monthly or weekly:
        pass
    elif warnings or problems:
        n = len(warnings) + len(problems)
        title = f"BalancePoint: {n} warning{'s' if n != 1 else ''}"
    else:
        title = "BalancePoint: daily"
    pace = pace_line(check)
    if pace:
        lines.append(pace)
    lines += warnings
    if weekly:
        lines += recurring_lines(conn, today)
    if report is not None:
        new = sum(a["added"] for a in report["accounts"])
        lines.append(f"Bank sync: {new} new transaction{'s' if new != 1 else ''}.")
    lines += problems
    uncategorized, guessed = to_categorize(conn)
    if uncategorized or guessed:
        lines.append(f"To categorize: {uncategorized + guessed} ({uncategorized} uncategorized, {guessed} guessed).")
    if cfg["app_url"]:
        lines.append(cfg["app_url"])
    while lines and not lines[-1]:
        lines.pop()
    return title, "\n".join(lines) + "\n"


# ---------------------------------------------------------------- sending

def send(cfg, data_dir, title, body):
    """Send by the configured method. Raises NotifyError with a reason that holds no secret."""
    if cfg["method"] == "email":
        return _send_email(cfg, data_dir, title, body)
    return _send_ntfy(cfg, title, body)


def _send_email(cfg, data_dir, title, body):
    password = load_password(data_dir) if cfg["smtp_user"] else None
    if cfg["smtp_user"] and not password:
        raise NotifyError(f"no SMTP password: run `run.py notify-setup-password` (or save it on the "
                          f"Notifications page, or set {PASSWORD_ENV}).")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = title, cfg["sender"], ", ".join(cfg["to"])
    msg.set_content(body)
    try:
        if cfg["smtp_port"] == 465:
            server = smtplib.SMTP_SSL(cfg["smtp_host"], 465, timeout=TIMEOUT, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=TIMEOUT)
        with server:
            if cfg["smtp_port"] != 465:
                server.starttls(context=ssl.create_default_context())
            if cfg["smtp_user"]:
                server.login(cfg["smtp_user"], password)
            server.send_message(msg, from_addr=cfg["sender"], to_addrs=cfg["to"])
    except smtplib.SMTPAuthenticationError:
        raise NotifyError("the mail server turned down the user name or password "
                          "(Gmail and others want an app password, not your usual one).") from None
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        raise NotifyError(_clean(f"email to {cfg['smtp_host']}:{cfg['smtp_port']} failed: {exc}", password)) from None


def strip_login(url):
    """(the link without a user:password@, the user:password or None)."""
    parts = urllib.parse.urlsplit(url)
    login, at, host = parts.netloc.rpartition("@")
    return (urllib.parse.urlunsplit(parts._replace(netloc=host)), login) if at else (url, None)


def _send_ntfy(cfg, title, body):
    url, login = strip_login(cfg["ntfy_url"])
    headers = {"Title": title.encode("ascii", "replace").decode(), "Tags": "moneybag"}
    if login:  # a protected topic on your own server: ntfy takes the login as Basic auth
        user, _, password = login.partition(":")
        pair = f"{urllib.parse.unquote(user)}:{urllib.parse.unquote(password)}"
        headers["Authorization"] = "Basic " + base64.b64encode(pair.encode("utf-8")).decode()
    request = urllib.request.Request(url, data=body.encode("utf-8"), method="POST", headers=headers)
    host = urllib.parse.urlsplit(url).hostname
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        raise NotifyError(f"ntfy server {host} answered {exc.code} {exc.reason}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise NotifyError(f"couldn't reach ntfy server {host}: {getattr(exc, 'reason', exc)}") from None


def _clean(text, secret):
    return text.replace(secret, "***") if secret else text


# ---------------------------------------------------------------- after a sync

def configured(settings, conn):
    """True when there's something to send by: a [notify] section, or settings saved and on in the page.
    A saved value that can't be read counts (config() reports it); a database that can't be read doesn't."""
    if settings.notify:
        return True
    try:
        raw = web_settings(conn)
    except PersonalConfigError:
        return True
    except Exception:  # noqa: BLE001
        return False
    return bool(raw and raw.get("enabled"))


def after_sync(settings, conn, data_dir, report=None, check=None, failure=None, today=None, out=print):
    """run.py simplefin-sync's last step. With notifications off it does nothing at all. Never raises."""
    if not configured(settings, conn):
        return False
    today = today or date.today()
    try:
        cfg = config(settings, conn)
        if cfg is None:
            return False
        if cfg["when"] == "monthly" and budgeting.get_setting(conn, MONTHLY_SENT_KEY, cast=str) == today.isoformat()[:7]:
            return False  # already sent this month (the Mac restarted on the 1st, say)
        if cfg["when"] == "weekly" and budgeting.get_setting(conn, WEEKLY_SENT_KEY, cast=str) == week_key(today):
            return False  # already sent this week
        message = build(conn, cfg, today, report, check, failure)
        if message is None:
            return False
        send(cfg, data_dir, *message)
    except (NotifyError, PersonalConfigError) as exc:
        out(f"  Notification not sent: {exc}")
        return False
    except Exception as exc:  # noqa: BLE001  a notification must never fail the sync
        out(f"  Notification not sent: {type(exc).__name__}")
        return False
    if cfg["when"] in ("monthly", "weekly"):
        sent = (MONTHLY_SENT_KEY, today.isoformat()[:7]) if cfg["when"] == "monthly" else (WEEKLY_SENT_KEY, week_key(today))
        budgeting.set_setting(conn, *sent)
        conn.commit()
    out(f"  Notification sent: {describe(cfg).rsplit(', ', 1)[0]}")
    return True
