from datetime import datetime

import pytest
from starlette.testclient import TestClient

from factory.cli import Context
from factory.server import create_app
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore
from factory.task import Task, TaskState


@pytest.fixture
def ctx(tmp_path):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt")
    settings.state_dir.mkdir()
    return Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={},
                   clock=lambda: datetime(2026, 1, 2, 3, 4, 5))


@pytest.fixture
def client(ctx):
    return TestClient(create_app(ctx))


def test_post_tasks_creates_a_proposed_task_and_returns_its_id(client, ctx):
    response = client.post("/tasks", json={"goal": "add multiply"})

    assert response.status_code == 201
    assert response.json() == {"id": "20260102-030405"}
    assert ctx.store.load("20260102-030405").goal == "add multiply"
