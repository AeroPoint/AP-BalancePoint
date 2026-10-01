"""Who the app answers, and the optional password. With nothing set, it behaves as it always has."""
import os
import stat

import pytest

from budget import create_app, trusted

DOCKER = {"REMOTE_ADDR": "172.17.0.1"}


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    def make(**env):
        for name in ("BUDGET_TRUSTED_NETWORKS", "BUDGET_PASSWORD", "BUDGET_PASSWORD_HASH", "BUDGET_SECRET_KEY"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("BUDGET_DATA_DIR", str(tmp_path / "data"))
        app = create_app()
        app.config["LOGIN_FAIL_DELAY"] = 0
        return app
    return make


def test_default_trust_is_unchanged():
    for address in ("127.0.0.1", "::1", "100.64.0.1", "100.127.255.254", "fd7a:115c:a1e0::5"):
        assert trusted(address), address
    for address in ("192.168.1.20", "10.0.0.5", "172.17.0.1", "100.128.0.1", "8.8.8.8", "", None, "nonsense"):
        assert not trusted(address), address


def test_default_has_no_login(make_app, tmp_path):
    app = make_app()
    client = app.test_client()
    assert app.config["SECRET_KEY"] == "local-only-budget-app"
    assert not app.config["LOGIN_REQUIRED"]
    r = client.get("/")
    assert r.status_code == 200 and b"Log out" not in r.data
    assert client.get("/login").status_code == 404
    assert client.get("/", environ_base=DOCKER).status_code == 403
    assert not (tmp_path / "data" / "secret-key").exists()


def test_trusted_networks_add_docker(make_app):
    client = make_app(BUDGET_TRUSTED_NETWORKS="172.16.0.0/12, 10.9.0.0/16").test_client()
    assert client.get("/", environ_base=DOCKER).status_code == 200
    assert client.get("/", environ_base={"REMOTE_ADDR": "10.9.3.4"}).status_code == 200
    assert client.get("/", environ_base={"REMOTE_ADDR": "100.101.1.2"}).status_code == 200  # Tailscale still
    assert client.get("/", environ_base={"REMOTE_ADDR": "192.168.1.20"}).status_code == 403


def test_a_bad_network_stops_the_app(make_app):
    with pytest.raises(SystemExit):
        make_app(BUDGET_TRUSTED_NETWORKS="172.17.0.0/99")


@pytest.mark.parametrize("setting", ["plain", "hash"])
def test_password_login_and_logout(make_app, setting):
    from werkzeug.security import generate_password_hash

    env = {"BUDGET_PASSWORD": "correct horse"} if setting == "plain" else \
          {"BUDGET_PASSWORD_HASH": generate_password_hash("correct horse")}
    client = make_app(**env).test_client()

    r = client.get("/transactions")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert client.post("/categories", data={}).status_code == 401
    assert client.get("/static/style.css").status_code == 200

    r = client.post("/login", data={"password": "wrong", "next": "/transactions"})
    assert r.status_code == 401 and b"Wrong password" in r.data
    assert client.get("/").status_code == 302

    r = client.post("/login", data={"password": "correct horse", "next": "/transactions"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/transactions")
    assert "SameSite=Lax" in r.headers["Set-Cookie"] and "HttpOnly" in r.headers["Set-Cookie"]
    page = client.get("/")
    assert page.status_code == 200 and b"Log out" in page.data

    assert client.get("/logout").status_code == 302
    assert client.get("/").status_code == 302


def test_password_keeps_the_trust_check_in_front(make_app):
    client = make_app(BUDGET_PASSWORD="pw").test_client()
    wifi = {"REMOTE_ADDR": "192.168.1.20"}
    assert client.get("/login", environ_base=wifi).status_code == 403
    assert client.post("/login", data={"password": "pw"}, environ_base=wifi).status_code == 403


def test_login_never_sends_you_off_site(make_app):
    client = make_app(BUDGET_PASSWORD="pw").test_client()
    for target in ("https://evil.example", "//evil.example", "/\\evil.example"):
        r = client.post("/login", data={"password": "pw", "next": target})
        assert r.headers["Location"] == "/", target


def test_secret_key_is_random_private_and_kept(make_app, tmp_path):
    app = make_app(BUDGET_PASSWORD="pw")
    path = tmp_path / "data" / "secret-key"
    assert app.config["SECRET_KEY"] == path.read_text().strip() != "local-only-budget-app"
    assert len(app.config["SECRET_KEY"]) >= 64
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert make_app(BUDGET_PASSWORD="pw").config["SECRET_KEY"] == app.config["SECRET_KEY"]  # same after a restart
    assert make_app(BUDGET_PASSWORD="pw", BUDGET_SECRET_KEY="from-env").config["SECRET_KEY"] == "from-env"
