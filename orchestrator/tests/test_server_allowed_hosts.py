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
