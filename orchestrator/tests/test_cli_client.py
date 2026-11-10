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
