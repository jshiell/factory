import io
from dataclasses import replace

import pytest

from factory.cli import IO, Context, main
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore


@pytest.fixture
def ctx(tmp_path):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt", watch_approvers=("alice",))
    settings.state_dir.mkdir()
    context = Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={})
    context.passes = []
    context.watch_pass = lambda settings: context.passes.append(settings)
    return context


def watch(ctx, *argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(["watch", *argv], IO(io.StringIO(), out, err), ctx)
    return code, out.getvalue(), err.getvalue()


def test_watch_once_runs_exactly_one_pass_and_exits_0(ctx):
    code, out, err = watch(ctx, "--once")

    assert code == 0
    assert ctx.passes == [ctx.settings]


def test_watch_without_approvers_says_to_set_watch_approvers_and_exits_1_without_a_pass(ctx):
    ctx.settings = replace(ctx.settings, watch_approvers=())

    code, out, err = watch(ctx, "--once")

    assert code == 1
    assert "watch_approvers" in err
    assert ctx.passes == []
