"""A lone surrogate in a stored task leaves the API as a JSON escape instead of breaking the response."""
from datetime import datetime

import pytest

from factory.cli import Context
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore
from factory.task import Task
from serving import served

TASK_ID = "20260102-030405"
SURROGATE_TEXT = "bad\ud800text"


@pytest.fixture
def ctx(tmp_path):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt")
    settings.state_dir.mkdir()
    return Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={},
                   clock=lambda: datetime(2026, 1, 2, 3, 4, 5))


@pytest.fixture
def client(ctx):
    with served(ctx) as client:
        yield client


def test_get_task_returns_a_surrogate_goal(client, ctx):
    ctx.store.save(Task(TASK_ID, "prompt", goal=SURROGATE_TEXT))

    response = client.get(f"/tasks/{TASK_ID}")

    assert response.status_code == 200
    assert response.json()["goal"] == SURROGATE_TEXT


def test_get_tasks_lists_a_task_with_a_surrogate_failure_reason(client, ctx):
    ctx.store.save(Task(TASK_ID, "prompt", failure_reason=SURROGATE_TEXT))

    response = client.get("/tasks")

    assert response.status_code == 200
    assert response.json()["tasks"][0]["failure_reason"] == SURROGATE_TEXT
