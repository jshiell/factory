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
