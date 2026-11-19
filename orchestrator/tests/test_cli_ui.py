import io
from datetime import datetime

import httpx2

from factory.cli import IO, Context, _parser, main
from factory.client import ServerClient
from factory.ledger import Ledger
from factory.settings import Settings
from factory.store import TaskStore

URL = "http://127.0.0.1:8765/ui/login?code=abc"


def nobody_listening(request):
    raise httpx2.ConnectError("refused")


def issues_a_link(request):
    assert (request.method, request.url.path, request.headers["authorization"]) == ("POST", "/ui-login-codes", "Bearer tok")
    return httpx2.Response(200, json={"url": URL})


def context(tmp_path, handler):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt")
    http = httpx2.Client(transport=httpx2.MockTransport(handler), base_url="http://127.0.0.1:8765")
    return Context(settings, TaskStore(settings.tasks_dir), Ledger(settings.ledger_path), stages={},
                   clock=lambda: datetime(2026, 1, 2, 3, 4, 5), server=ServerClient(http, "tok"))


def run(ctx, *argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), IO(io.StringIO(), out, err), ctx)
    return code, out.getvalue(), err.getvalue()


def test_ui_prints_the_login_url_the_server_issues(tmp_path):
    assert run(context(tmp_path, issues_a_link), "ui") == (0, URL + "\n", "")


def test_guard_ui_with_no_server_running_says_to_start_one(tmp_path):
    code, out, err = run(context(tmp_path, nobody_listening), "ui")

    assert (code, out) == (1, "")
    assert "no factory server is running" in err


def test_ui_help_says_what_it_does(tmp_path):
    assert "Print a one-time link that logs a browser into the web UI" in " ".join(_parser().format_help().split())
