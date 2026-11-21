"""The web UI over plain HTTP on a host listed in `server_allowed_hosts`, where browsers send no Fetch Metadata."""
from datetime import datetime

import pytest
from starlette.testclient import TestClient

from factory.cli import Context
from factory.server import create_secured_app, load_or_create_token
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore

REMOTE = "http://nas.lan:8765"


@pytest.fixture
def ctx(tmp_path):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt",
                        server_allowed_hosts=("nas.lan", "192.168.1.5"))
    settings.state_dir.mkdir()
    return Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={},
                   clock=lambda: datetime(2026, 1, 2, 3, 4, 5))


@pytest.fixture
def client(ctx):
    with TestClient(create_secured_app(ctx, load_or_create_token(ctx.settings.state_dir)), base_url=REMOTE) as client:
        yield client


@pytest.fixture
def signed_in(client):
    response = client.get("/ui/login", params={"code": client.app.state.login_codes.issue()})
    assert response.status_code == 200
    return client


def test_guard_sign_in_on_a_listed_host_sets_the_port_named_cookie(client):
    response = client.get("/ui/login", params={"code": client.app.state.login_codes.issue()})

    assert response.status_code == 200
    assert response.headers["set-cookie"].startswith("factory_session_8765=")


def test_guard_a_read_with_the_cookie_and_no_fetch_metadata_is_served(signed_in):
    response = signed_in.get("/ui/")

    assert response.status_code == 200
    assert not any(name.startswith("sec-fetch-") for name in response.request.headers)
