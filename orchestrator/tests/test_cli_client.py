import io
from datetime import datetime

import httpx
import pytest

from factory.cli import IO, Context, main
from factory.client import ServerClient
from factory.ledger import Ledger
from factory.settings import Settings
from factory.store import TaskStore


def nobody_listening(request):
    raise httpx.ConnectError("refused")


@pytest.fixture
def ctx(tmp_path):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt")
    http = httpx.Client(transport=httpx.MockTransport(nobody_listening), base_url="http://127.0.0.1:8765")
    return Context(settings, TaskStore(settings.tasks_dir), Ledger(settings.ledger_path), stages={},
                   clock=lambda: datetime(2026, 1, 2, 3, 4, 5), server=ServerClient(http, "tok"))


def run(ctx, *argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), IO(io.StringIO(), out, err), ctx)
    return code, out.getvalue(), err.getvalue()


def test_a_command_with_no_server_running_says_to_start_one(ctx):
    code, out, err = run(ctx, "list")

    assert (code, out) == (1, "")
    assert "no factory server" in err and "factory serve" in err


def test_ctrl_c_while_waiting_stops_waiting_and_names_the_job_that_keeps_running(ctx):
    def handler(request):
        if request.method == "POST":
            return httpx.Response(202, json={"job": {"id": "j7", "state": "queued"}})
        return httpx.Response(200, json={"id": "j7", "state": "running", "result": None, "error": None})

    def interrupted(seconds):
        raise KeyboardInterrupt

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:8765")
    ctx.server = ServerClient(http, "tok", sleep=interrupted)

    code, out, err = run(ctx, "advance", "t1")

    assert code == 130 and "j7" in err and "still running" in err


def test_a_server_that_refuses_the_token_says_where_the_token_comes_from(ctx):
    def unauthorized(request):
        return httpx.Response(401, json={"error": "unauthorized", "detail": "bearer token required"})

    http = httpx.Client(transport=httpx.MockTransport(unauthorized), base_url="http://127.0.0.1:8765")
    ctx.server = ServerClient(http, "stale")

    code, out, err = run(ctx, "list")

    assert (code, out) == (1, "")
    assert str(ctx.settings.state_dir / "token") in err
