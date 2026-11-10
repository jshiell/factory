import io
from datetime import datetime

import pytest

from factory.cli import IO, Context, main
from factory.ledger import Ledger
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore
from factory.store import TaskStore
from factory.task import Task, TaskState


@pytest.fixture
def ctx(tmp_path):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt")
    settings.state_dir.mkdir()
    return Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={},
                   clock=lambda: datetime(2026, 1, 2, 3, 4, 5))


def run(ctx, *argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), IO(io.StringIO(), out, err), ctx)
    return code, out.getvalue(), err.getvalue()


def seed_old_files(settings):
    settings.tasks_dir.mkdir()
    TaskStore(settings.tasks_dir).save(Task(id="t1", prompt="", state=TaskState.PROPOSED, goal="old goal"))
    Ledger(settings.ledger_path).append("t1", 0.25, stage="plan")


def test_migrate_imports_the_old_files_and_reports_the_counts(ctx):
    seed_old_files(ctx.settings)

    code, out, _ = run(ctx, "migrate")

    assert code == 0
    assert out == "migrated 1 tasks, 1 ledger entries\n"
    assert ctx.store.load("t1").goal == "old goal"
    assert ctx.ledger.total() == 0.25
