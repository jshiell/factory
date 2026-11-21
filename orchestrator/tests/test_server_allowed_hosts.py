"""The guard accepts the `Host` names listed in `server_allowed_hosts`, and nothing else beyond loopback."""
from datetime import datetime

import pytest
from starlette.testclient import TestClient

from factory.cli import Context
from factory.server import create_secured_app, load_or_create_token
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore


def make_ctx(tmp_path, allowed_hosts):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt",
                        server_allowed_hosts=allowed_hosts)
    settings.state_dir.mkdir()
    return Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={},
                   clock=lambda: datetime(2026, 1, 2, 3, 4, 5))


@pytest.fixture
def ctx(tmp_path):
    return make_ctx(tmp_path, ("nas.lan", "192.168.1.5"))


@pytest.fixture
def token(ctx):
    return load_or_create_token(ctx.settings.state_dir)


@pytest.fixture
def client(ctx, token):
    with TestClient(create_secured_app(ctx, token), base_url="http://127.0.0.1:8765") as client:
        yield client


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("host", ["nas.lan:8765", "nas.lan", "192.168.1.5:8765"])
def test_a_listed_host_is_served_with_the_token(client, token, host):
    assert client.get("/tasks", headers={**auth(token), "Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["evil.example", "NAS.lan", "nas.lan.", "nas.lan.:8765", "0.0.0.0", "nas.lan.evil.example",
                                  "evil.nas.lan", "nas.lan@evil.example", "[nas.lan]", "192.168.1.50", ""])
def test_guard_an_unlisted_or_unnormalised_host_is_still_403(client, token, host):
    assert client.get("/tasks", headers={**auth(token), "Host": host}).status_code == 403


def test_guard_without_a_list_a_lan_name_is_403(tmp_path):
    ctx = make_ctx(tmp_path, ())
    token = load_or_create_token(ctx.settings.state_dir)
    with TestClient(create_secured_app(ctx, token)) as client:
        assert client.get("/tasks", headers={**auth(token), "Host": "127.0.0.1:8765"}).status_code == 200
        assert client.get("/tasks", headers={**auth(token), "Host": "nas.lan:8765"}).status_code == 403


@pytest.mark.parametrize("origin", ["http://nas.lan:8765", "http://nas.lan", "null"])
def test_guard_any_origin_on_the_api_is_403_on_a_listed_host(client, token, origin):
    headers = {**auth(token), "Host": "nas.lan:8765", "Origin": origin}

    assert client.get("/tasks", headers=headers).status_code == 403
    assert client.post("/tasks", json={"goal": "g"}, headers=headers).status_code == 403
