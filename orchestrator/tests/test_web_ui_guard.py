from datetime import datetime

import pytest
from starlette.testclient import TestClient

from factory.cli import Context
from factory.server import create_secured_app, load_or_create_token
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore


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


def test_the_ui_without_a_session_is_401_html_naming_the_command(client):
    response = client.get("/ui/")

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("text/html")
    assert "factory ui" in response.text


def test_the_ui_with_only_the_bearer_token_is_401(client, token):
    assert client.get("/ui/", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_guard_the_api_is_not_opened_by_a_same_origin_origin(client, token):
    headers = {"Authorization": f"Bearer {token}", "Origin": "http://127.0.0.1:8765"}

    assert client.get("/tasks", headers=headers).status_code == 403


def test_guard_a_path_that_only_starts_with_ui_is_the_api(client):
    assert client.post("/ui-login-codes").status_code == 401
    assert client.post("/ui-login-codes").headers["content-type"] == "application/json"


@pytest.fixture
def signed_in(client):
    client.get("/ui/login", params={"code": client.app.state.login_codes.issue()})
    return client


@pytest.mark.parametrize("origin", ["http://evil.example", "http://127.0.0.1:9", "https://127.0.0.1:8765", "null"])
def test_a_foreign_origin_is_403_even_with_the_session(signed_in, origin):
    assert signed_in.get("/ui/", headers={"Origin": origin}).status_code == 403


def test_the_exact_origin_is_served(signed_in):
    assert signed_in.get("/ui/", headers={"Origin": "http://127.0.0.1:8765"}).status_code == 200


@pytest.mark.parametrize("site", ["same-site", "cross-site"])
def test_a_request_from_another_site_is_403_even_with_the_session(signed_in, site):
    assert signed_in.get("/ui/", headers={"Sec-Fetch-Site": site}).status_code == 403


@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_a_same_origin_or_user_initiated_request_is_served(signed_in, site):
    assert signed_in.get("/ui/", headers={"Sec-Fetch-Site": site}).status_code == 200


def test_guard_a_non_loopback_host_is_403_on_the_ui(signed_in):
    assert signed_in.get("/ui/", headers={"Host": "evil.example"}).status_code == 403


def test_login_needs_no_session_but_still_refuses_a_foreign_origin_and_site(client):
    code = client.app.state.login_codes.issue()

    assert client.get("/ui/login", params={"code": code}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.get("/ui/login", params={"code": code}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/ui/login", params={"code": code}).status_code == 200


UI_SECURITY_HEADERS = {
    "content-security-policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; "
                                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "referrer-policy": "same-origin",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "cross-origin-resource-policy": "same-origin",
}


def assert_ui_security_headers(response):
    for name, value in UI_SECURITY_HEADERS.items():
        assert response.headers.get_list(name) == [value], name


def test_the_ui_page_carries_the_security_headers(signed_in):
    assert_ui_security_headers(signed_in.get("/ui/"))


def test_the_401_page_carries_the_security_headers(client):
    response = client.get("/ui/")

    assert response.status_code == 401
    assert_ui_security_headers(response)


def test_the_403_page_carries_the_security_headers(signed_in):
    response = signed_in.get("/ui/", headers={"Origin": "http://evil.example"})

    assert response.status_code == 403
    assert_ui_security_headers(response)


def test_the_host_refusal_on_the_ui_carries_the_security_headers(signed_in):
    assert_ui_security_headers(signed_in.get("/ui/", headers={"Host": "evil.example"}))


def test_the_slashless_redirect_carries_the_security_headers(signed_in):
    response = signed_in.get("/ui", follow_redirects=False)

    assert response.status_code == 308
    assert_ui_security_headers(response)


def test_api_responses_do_not_get_the_ui_headers(client, token):
    response = client.get("/tasks", headers={"Authorization": f"Bearer {token}"})

    assert "content-security-policy" not in response.headers
    assert "cache-control" not in response.headers
