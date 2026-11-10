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


@pytest.mark.parametrize("body", [{}, {"goal": ""}, {"goal": 5}, [], "text"])
def test_post_tasks_without_a_goal_string_is_422_and_creates_nothing(client, ctx, body):
    response = client.post("/tasks", json=body)

    assert response.status_code == 422
    assert response.json()["error"] == "invalid input"
    assert ctx.store.list().tasks == []


def test_post_tasks_with_a_body_that_is_not_json_is_422(client):
    response = client.post("/tasks", content=b"{nope", headers={"content-type": "application/json"})

    assert response.status_code == 422


def test_get_tasks_lists_ids_states_and_unreadable_rows(client, ctx):
    ctx.store.save(Task(id="a", prompt="", state=TaskState.PROPOSED))
    ctx.store.save(Task(id="b", prompt="", state=TaskState.FAILED, failure_reason="gate_failed"))

    response = client.get("/tasks")

    assert response.status_code == 200
    assert response.json() == {
        "tasks": [{"id": "a", "state": "proposed", "failure_reason": None},
                  {"id": "b", "state": "failed", "failure_reason": "gate_failed"}],
        "skipped": [],
    }
