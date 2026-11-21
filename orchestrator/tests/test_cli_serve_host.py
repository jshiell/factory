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
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt")
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
    assert out == f"serving on http://127.0.0.1:8765; token in {ctx.settings.state_dir / 'token'}\n"
