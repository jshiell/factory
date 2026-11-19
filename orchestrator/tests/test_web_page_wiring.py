from datetime import datetime

import pytest
from starlette.testclient import TestClient

from factory.cli import Context
from factory.server import create_secured_app, load_or_create_token
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore


@pytest.fixture
def client(tmp_path):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt")
    settings.state_dir.mkdir()
    ctx = Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={},
                  clock=lambda: datetime(2026, 1, 2, 3, 4, 5))
    with TestClient(create_secured_app(ctx, load_or_create_token(settings.state_dir)), base_url="http://127.0.0.1:8765") as client:
        client.get("/ui/login", params={"code": client.app.state.login_codes.issue()})
        yield client


def test_the_table_is_wrapped_in_an_sse_subscription_to_the_tasks_event(client):
    page = client.get("/ui/").text

    assert '<div id="tasks" hx-ext="sse" sse-connect="/ui/events" sse-swap="tasks">' in page


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
