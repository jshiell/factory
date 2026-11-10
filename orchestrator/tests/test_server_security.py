import os
import stat
from datetime import datetime

import pytest
from starlette.testclient import TestClient

from factory.cli import Context
from factory.server import TokenFileUnsafe, create_secured_app, load_or_create_token
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


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_the_token_is_created_once_private_and_reused(ctx):
    first = load_or_create_token(ctx.settings.state_dir)

    assert len(first) >= 32
    assert stat.S_IMODE(os.stat(ctx.settings.state_dir / "token").st_mode) == 0o600
    assert load_or_create_token(ctx.settings.state_dir) == first


def test_a_token_file_others_can_read_is_refused(ctx):
    load_or_create_token(ctx.settings.state_dir)
    os.chmod(ctx.settings.state_dir / "token", 0o644)

    with pytest.raises(TokenFileUnsafe):
        load_or_create_token(ctx.settings.state_dir)


def test_a_request_with_the_token_is_served(client, token):
    assert client.get("/tasks", headers=auth(token)).status_code == 200


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer "}])
def test_a_missing_or_wrong_token_is_401_on_every_method(client, headers):
    assert client.get("/tasks", headers=headers).status_code == 401
    assert client.post("/tasks", json={"goal": "g"}, headers=headers).status_code == 401
    assert client.get("/jobs", headers=headers).status_code == 401


def test_an_unauthorised_request_changes_nothing(client, ctx):
    client.post("/tasks", json={"goal": "g"})

    assert ctx.store.list().tasks == []


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8765", "127.0.0.1.evil.example", "0.0.0.0", ""])
def test_a_non_loopback_host_is_403_even_with_the_token(client, token, host):
    assert client.get("/tasks", headers={**auth(token), "Host": host}).status_code == 403


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:9", "localhost", "localhost:8765", "[::1]:8765"])
def test_loopback_hosts_are_served(client, token, host):
    assert client.get("/tasks", headers={**auth(token), "Host": host}).status_code == 200


@pytest.mark.parametrize("origin", ["http://evil.example", "http://127.0.0.1:8765", "null"])
def test_any_origin_header_is_403_even_with_the_token(client, token, origin):
    assert client.get("/tasks", headers={**auth(token), "Origin": origin}).status_code == 403
