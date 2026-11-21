"""The web UI over plain HTTP on a host listed in `server_allowed_hosts`, where browsers send no Fetch Metadata."""
from datetime import datetime

import pytest
from starlette.testclient import TestClient

from factory.cli import Context
from factory.server import create_secured_app, load_or_create_token
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore
from factory.task import TaskState
from webhelpers import add

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


def test_ui_responses_send_referrer_policy_same_origin_so_a_plain_http_form_post_names_its_origin(signed_in):
    assert signed_in.get("/ui/").headers.get_list("referrer-policy") == ["same-origin"]


def reject(client, headers):
    return client.post("/ui/tasks/20260102-030405/reject", headers=headers, follow_redirects=False)


def test_guard_a_reject_post_naming_its_own_origin_without_fetch_metadata_is_served(signed_in, ctx):
    add(ctx, "20260102-030405", TaskState.REVIEWED)

    response = reject(signed_in, {"Origin": REMOTE})

    assert response.status_code == 303
    assert not any(name.startswith("sec-fetch-") for name in response.request.headers)
    assert ctx.store.load("20260102-030405").state is TaskState.FAILED


@pytest.mark.parametrize("origin", ["http://192.168.1.5:8765", "http://127.0.0.1:8765", "http://nas.lan",
                                    "https://nas.lan:8765", "null"])
def test_guard_a_reject_post_from_another_origin_or_null_without_fetch_metadata_is_403(signed_in, ctx, origin):
    add(ctx, "20260102-030405", TaskState.REVIEWED)

    assert reject(signed_in, {"Origin": origin}).status_code == 403
    assert ctx.store.load("20260102-030405").state is TaskState.REVIEWED
