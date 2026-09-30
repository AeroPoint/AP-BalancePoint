"""The demo household, and every page rendering on it."""
from argparse import Namespace

import pytest

PAGES = ["/", "/plan", "/trends", "/business", "/transactions", "/categorize", "/net-worth", "/bankroll",
         "/accounts", "/upload", "/rules", "/categories", "/transactions?flag=check", "/categorize?mode=guessed",
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
