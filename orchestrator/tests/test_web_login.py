from datetime import datetime

import pytest
from starlette.testclient import TestClient

from factory.cli import Context
from factory.server import create_secured_app, load_or_create_token
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore

COOKIE = "factory_session_8765"


@pytest.fixture
def ctx(tmp_path):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt")
    settings.state_dir.mkdir()
    return Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={},
                   clock=lambda: datetime(2026, 1, 2, 3, 4, 5))


@pytest.fixture
def token(ctx):
    return load_or_create_token(ctx.settings.state_dir)


@pytest.fixture
def client(ctx, token):
    with TestClient(create_secured_app(ctx, token), base_url="http://127.0.0.1:8765") as client:
        yield client


def login(client):
    return client.get("/ui/login", params={"code": client.app.state.login_codes.issue()})


def test_a_valid_code_answers_with_a_same_origin_link_not_a_redirect(client):
    response = login(client)

    assert response.status_code == 200
    assert 'href="/ui/"' in response.text


def test_login_sets_a_strict_http_only_cookie_scoped_to_ui_named_for_the_port(client):
    set_cookie = login(client).headers["set-cookie"]

    assert set_cookie.startswith(f"{COOKIE}=")
    assert "HttpOnly" in set_cookie
    assert "SameSite=strict" in set_cookie
    assert "Path=/ui" in set_cookie


def test_a_used_code_is_401(client):
    code = client.app.state.login_codes.issue()
    client.get("/ui/login", params={"code": code})
    client.cookies.clear()

    assert client.get("/ui/login", params={"code": code}).status_code == 401


def test_a_missing_or_unknown_code_is_401(client):
    assert client.get("/ui/login").status_code == 401
    assert client.get("/ui/login", params={"code": "nope"}).status_code == 401


def test_with_the_session_cookie_the_ui_is_served(client):
    login(client)

    assert client.get("/ui/").status_code == 200


def test_an_unknown_session_cookie_is_401(client):
    client.cookies.set(COOKIE, "forged", path="/ui")

    assert client.get("/ui/").status_code == 401


def test_guard_a_session_cookie_does_not_open_the_api(client):
    login(client)

    assert client.get("/tasks").status_code == 401


def test_the_slashless_ui_redirects_to_the_canonical_page(client):
    login(client)

    response = client.get("/ui", follow_redirects=False)

    assert response.status_code == 308
    assert response.headers["location"] == "/ui/"


def test_the_code_endpoint_returns_a_login_url_for_the_requests_host_that_logs_in(client, token):
    response = client.post("/ui-login-codes", headers={"Authorization": f"Bearer {token}", "Host": "localhost:8765"})

    url = response.json()["url"]
    assert response.status_code == 200
    assert url.startswith("http://localhost:8765/ui/login?code=")
    assert client.get(url.removeprefix("http://localhost:8765")).status_code == 200


def test_guard_the_code_endpoint_needs_the_bearer_token(client):
    assert client.post("/ui-login-codes").status_code == 401
