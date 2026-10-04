"""The demo household, and every page rendering on it."""
from argparse import Namespace

import pytest

PAGES = ["/", "/plan", "/trends", "/business", "/transactions", "/categorize", "/net-worth", "/bankroll",
         "/accounts", "/bank-sync", "/upload", "/rules", "/categories", "/transactions?flag=check", "/categorize?mode=guessed",
         "/?view=year", "/transactions?source=mcc"]


def test_demo_builds_a_household(demo_conn):
    n = demo_conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    assert n > 900
    # Rules categorize nearly all of it; the bakery has no rule, so it's a guess from its card type to confirm.
    by_rule = demo_conn.execute("SELECT COUNT(*) FROM transactions WHERE category_source = 'rule'").fetchone()[0]
    guessed = demo_conn.execute("SELECT COUNT(*) FROM transactions WHERE category_source = 'mcc'").fetchone()[0]
    assert by_rule > 0.9 * n and guessed > 0
    assert demo_conn.execute("SELECT COUNT(*) FROM bj_sessions").fetchone()[0] == 5


@pytest.mark.parametrize("path", PAGES)
def test_every_page_renders(demo_conn, client, path):
    r = client.get(path)
    assert r.status_code == 200, path
    assert b"Traceback" not in r.data


def test_pages_render_with_no_data(client):
    for path in PAGES:
        assert client.get(path).status_code == 200, path


def test_only_this_computer_and_tailscale(client):
    assert client.get("/", environ_base={"REMOTE_ADDR": "100.101.1.2"}).status_code == 200  # Tailscale
    assert client.get("/", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 403  # same Wi-Fi


def test_demo_refuses_the_real_data_folder(tmp_path, monkeypatch):
    import run

    real = tmp_path / "real"
    real.mkdir()
    (real / "budget.db").write_bytes(b"precious")
    monkeypatch.setenv("BUDGET_DATA_DIR", str(real))
    for target in (real, real / "inside", tmp_path):
        with pytest.raises(SystemExit):
            run.build_demo(Namespace(dir=str(target), replace=True))
    assert (real / "budget.db").read_bytes() == b"precious"


def test_demo_replaces_only_its_own_folder(tmp_path, monkeypatch):
    import run

    monkeypatch.setenv("BUDGET_DATA_DIR", str(tmp_path / "real"))
    other = tmp_path / "someone-elses"
    other.mkdir()
    (other / "budget.db").write_bytes(b"not a demo")
    with pytest.raises(SystemExit):
        run.build_demo(Namespace(dir=str(other), replace=True))
    assert (other / "budget.db").read_bytes() == b"not a demo"

    target = tmp_path / "demo"
    run.build_demo(Namespace(dir=str(target), replace=False))
    run.build_demo(Namespace(dir=str(target), replace=True))  # a demo folder can be rebuilt
    with pytest.raises(SystemExit):
        run.build_demo(Namespace(dir=str(target), replace=False))


def test_plan_shows_the_saved_backup_accounts(demo_conn, client):
    """The backup selects used the inner loop's index, so saving the plan cleared them."""
    import re

    html = client.get("/plan").get_data(as_text=True)
    first = re.search(r'name="plan_backup_account">(.*?)</select>', html, re.S).group(1)
    second = re.search(r'name="plan_backup_account_2">(.*?)</select>', html, re.S).group(1)
    assert re.search(r"selected>Savings<", first) and re.search(r"selected>Brokerage<", second)


def test_blackjack_is_off_for_a_new_install(client):
    html = client.get("/").get_data(as_text=True)
    assert 'href="/bankroll"' not in html and "Blackjack tracker" not in client.get("/upload").get_data(as_text=True)
    assert 'aria-label="Main"' in html and ">Net worth" in html


def test_blackjack_stays_on_once_there_are_sessions(demo_conn, client):
    """An install that uses it (like the one this came from) keeps it without setting anything."""
    html = client.get("/").get_data(as_text=True)
    assert 'href="/bankroll"' in html and "Blackjack tracker" in client.get("/upload").get_data(as_text=True)


@pytest.mark.parametrize("setting, sessions, shown", [("false", True, False), ("true", False, True)])
def test_features_setting_wins(tmp_path, monkeypatch, setting, sessions, shown):
    from budget import create_app, demo
    from budget.db import connect

    data = tmp_path / "data"
    data.mkdir()
    (data / "personal.toml").write_text(f"[features]\nblackjack = {setting}\n")
    monkeypatch.setenv("BUDGET_DATA_DIR", str(data))
    app = create_app()
    if sessions:
        conn = connect(app.config["DATABASE"])
        demo.build(conn)
        conn.close()
    assert ('href="/bankroll"' in app.test_client().get("/").get_data(as_text=True)) == shown


def test_upload_page_without_a_spreadsheet_setup_is_a_quiet_note(client):
    """Most people have no old workbook: a collapsed hint, not an error or an import form that can't work."""
    html = client.get("/upload").get_data(as_text=True)
    assert "Have an old budget spreadsheet?" in html and "personal.example.toml" in html
    assert "flash error" not in html and "Import workbook" not in html
    assert "app&#39;s data folder" in html or "app's data folder" in html
    assert "which git ignores" not in html  # not true in Docker, where the data is a volume


@pytest.mark.parametrize("path", PAGES)
def test_pages_load_nothing_from_google(demo_conn, client, path):
    """Fonts are bundled, so opening a page contacts no one."""
    html = client.get(path).get_data(as_text=True)
    assert "googleapis" not in html and "gstatic" not in html


def test_bundled_font_is_served(client):
    css = client.get("/static/style.css").get_data(as_text=True)
    assert "@font-face" in css and "fonts/LibreFranklin-latin.woff2" in css and "googleapis" not in css
    r = client.get("/static/fonts/LibreFranklin-latin.woff2")
    assert r.status_code == 200 and r.data[:4] == b"wOF2"
    r.close()
    r = client.get("/static/fonts/OFL.txt")
    assert r.status_code == 200 and b"SIL Open Font License" in r.data
    r.close()
