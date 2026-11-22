"""Fixtures for the web merge tests: a real repo and an audited task on its own worktree, as test_server_merge sets up.

A test module loads them with pytest_plugins = ["webfixtures", "webmerge"]; `ctx` here replaces webfixtures' one so the
settings point at the git repo."""
from datetime import datetime

import pytest

from factory.cli import Context
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore
from factory.task import Task, TaskState
from factory.worktrees import WorktreeManager
from gitutil import git

TASK_ID = "20260102-030405"


@pytest.fixture
def ctx(tmp_path, repo):
    settings = Settings(repo=repo, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt")
    settings.state_dir.mkdir()
    return Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={},
                   clock=lambda: datetime(2026, 1, 2, 3, 4, 5))


def _audit_task(ctx, repo, filename="b.txt", **fields) -> Task:
    worktree, base = WorktreeManager(repo, ctx.settings.worktree_root).create(TASK_ID)
    (worktree / filename).write_text("b")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "Task")
    task = Task(id=TASK_ID, state=TaskState.AUDITED, worktree=str(worktree), base_sha=base,
                audited_sha=git(worktree, "rev-parse", "HEAD"), **{"prompt": "p", "goal": "fix the widget", **fields})
    ctx.store.save(task)
    return task


@pytest.fixture
def audit(ctx, repo):
    """`audit(filename=..., **fields)` saves an AUDITED task whose worktree holds one commit adding `filename`."""
    return lambda filename="b.txt", **fields: _audit_task(ctx, repo, filename, **fields)


@pytest.fixture
def audited(audit) -> Task:
    return audit(audit_findings=("looks fine",))
