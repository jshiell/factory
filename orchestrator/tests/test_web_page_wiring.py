from datetime import datetime

import pytest
from starlette.testclient import TestClient

from factory.cli import Context
from factory.server import load_or_create_token
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore
from serving import repos_app


@pytest.fixture
def client(tmp_path):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt")
    settings.state_dir.mkdir()
    ctx = Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={},
                  clock=lambda: datetime(2026, 1, 2, 3, 4, 5))
    with TestClient(repos_app(ctx, load_or_create_token(settings.state_dir)), base_url="http://127.0.0.1:8765") as client:
        client.get("/ui/login", params={"code": client.app.state.login_codes.issue()})
        yield client


def test_the_table_is_wrapped_in_an_sse_subscription_to_the_repos_event(client):
    page = client.get("/ui/").text

    assert '<div id="repos" hx-ext="sse" sse-connect="/ui/events" sse-swap="repos">' in page


def test_the_page_has_a_hidden_disconnected_banner_and_loads_ui_js(client):
    page = client.get("/ui/").text

    assert '<script src="/ui/static/ui.js">' in page
    assert 'id="disconnected" hidden' in page
    assert "factory ui" in page
    assert "<script>" not in page and "style=" not in page


def test_ui_js_is_served_without_a_session_and_with_the_security_headers(client):
    client.cookies.clear()

    response = client.get("/ui/static/ui.js")

    assert response.status_code == 200
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
    assert response.headers["cache-control"] == "no-store"
    for hook in ("htmx:sseError", "htmx:sseOpen", "disconnected"):
        assert hook in response.text


def test_ui_js_closes_streams_when_the_tab_is_hidden(client):
    script = client.get("/ui/static/ui.js").text

    for hook in ("createEventSource", "visibilitychange"):
        assert hook in script


def test_ui_js_reopens_the_stream_when_the_tab_is_shown_again(client):
    script = client.get("/ui/static/ui.js").text

    for hook in ("htmx.process", "data-sse-epoch"):
        assert hook in script


def test_ui_js_loads_after_the_banner_so_the_body_exists_when_it_runs(client):
    page = client.get("/ui/").text

    assert page.index('<script src="/ui/static/ui.js">') > page.index('id="disconnected"')
