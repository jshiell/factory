import io
from dataclasses import replace

import pytest

from factory.cli import IO, Context, main
from factory.process_registry import ReapResult
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore


@pytest.fixture
def ctx(tmp_path):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt", home=tmp_path / "home")
    settings.state_dir.mkdir()
    context = Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={})
    context.reap = lambda directory: ReapResult([], [])
    context.served = []
    context.serve = lambda app, host, port: context.served.append((app, host, port))
    return context


def serve(ctx, *argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(["serve", *argv], IO(io.StringIO(), out, err), ctx)
    return code, out.getvalue(), err.getvalue()


def test_serve_binds_the_configured_host_and_still_prints_the_loopback_url(ctx):
    ctx.settings = replace(ctx.settings, server_host="0.0.0.0")

    code, out, _ = serve(ctx)

    assert code == 0
    (_, host, port), = ctx.served
    assert (host, port) == ("0.0.0.0", 8765)
    assert out == f"serving on http://127.0.0.1:8765; token in {ctx.settings.token_path}\n"


def test_a_wide_bind_warns_on_stderr_before_it_starts_listening(ctx):
    out, err = io.StringIO(), io.StringIO()
    heard_before_listening = []
    ctx.serve = lambda app, host, port: heard_before_listening.append(err.getvalue())

    main(["serve", "--host", "0.0.0.0", "--port", "9001"], IO(io.StringIO(), out, err), ctx)

    warning = (
        "warning: bound to 0.0.0.0, so port 9001 is reachable from the network; "
        "requests whose Host is not loopback are still refused (403)\n"
    )
    assert err.getvalue() == warning
    assert heard_before_listening == [warning]
    assert out.getvalue().splitlines() == [f"serving on http://127.0.0.1:9001; token in {ctx.settings.token_path}"]


def test_the_cli_client_still_connects_to_loopback_whatever_the_bind(ctx):
    from factory.cli import _server_client
    settings = replace(ctx.settings, server_host="0.0.0.0", server_port=9001)

    client = _server_client(settings)

    assert str(client._http.base_url).rstrip("/") == "http://127.0.0.1:9001"


def test_a_loopback_bind_writes_nothing_to_stderr(ctx):
    code, _, err = serve(ctx)

    assert code == 0 and err == ""


def test_a_refused_wide_bind_does_not_warn(ctx):
    from factory.lock import state_lock
    with state_lock(ctx.settings.state_dir):
        code, _, err = serve(ctx, "--host", "0.0.0.0")

    assert code == 1 and "another factory run holds the lock" in err and "warning" not in err


def test_the_host_flag_refuses_any_other_value_and_serves_nothing(ctx):
    code, out, err = serve(ctx, "--host", "example.com")

    assert code == 2
    assert ctx.served == []
    assert out == ""
    assert "--host" in err and "127.0.0.1" in err and "0.0.0.0" in err


@pytest.mark.parametrize("configured, flag", [("127.0.0.1", "0.0.0.0"), ("0.0.0.0", "127.0.0.1")])
def test_the_host_flag_overrides_the_configured_host(ctx, configured, flag):
    ctx.settings = replace(ctx.settings, server_host=configured)

    serve(ctx, "--host", flag)

    assert ctx.served[0][1] == flag
