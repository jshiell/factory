from dataclasses import replace
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
def app(ctx):
    return create_app(ctx)


@pytest.fixture
def client(app):
    with TestClient(app) as client:  # entering runs the lifespan: the job worker starts
        yield client


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


def test_get_task_returns_every_field_of_the_task(client, ctx):
    ctx.store.save(Task(id="a", prompt="", state=TaskState.REVIEWED, goal="g", plan="p", plan_risks=("r",)))

    response = client.get("/tasks/a")

    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["state"], body["goal"], body["plan"], body["plan_risks"]) == ("a", "reviewed", "g", "p", ["r"])


@pytest.mark.parametrize("path", ["/tasks/nope", "/tasks/a.b"])
def test_get_unknown_or_unsafe_task_id_is_404_with_the_error_shape(client, path):
    response = client.get(path)

    assert response.status_code == 404
    assert response.json()["error"] == "unknown task"


def test_get_costs_reports_spend_by_stage_and_task(client, ctx):
    ctx.ledger.append("a", 0.25, stage="plan")
    ctx.ledger.append("a", 0.5, assumed=True, stage="implement")

    response = client.get("/costs")

    assert response.status_code == 200
    assert response.json() == {"by_stage": {"plan": 0.25, "implement": 0.5}, "by_task": {"a": 0.75},
                               "assumed": 0.5, "total": 0.75}


def test_get_costs_for_an_unknown_task_is_404(client):
    assert client.get("/costs", params={"task_id": "nope"}).status_code == 404


def stage_to(ctx, state):
    def run(task_id):
        moved = replace(ctx.store.load(task_id), state=state, plan="1. do it")
        ctx.store.save(moved)
        return moved
    return run


def test_advance_returns_202_with_a_job_that_then_succeeds_with_the_steps(client, app, ctx):
    ctx.stages["plan"] = stage_to(ctx, TaskState.PLANNED)
    ctx.stages["review"] = stage_to(ctx, TaskState.REVIEWED)
    ctx.store.save(Task(id="a", prompt="", state=TaskState.PROPOSED))

    response = client.post("/tasks/a/advance")

    assert response.status_code == 202
    job_id = response.json()["job"]["id"]
    app.state.runner.wait_idle()
    job = client.get(f"/jobs/{job_id}").json()
    assert (job["state"], job["kind"], job["task_id"]) == ("succeeded", "advance", "a")
    assert job["result"] == {"steps": [{"stage": "plan", "state": "planned"}, {"stage": "review", "state": "reviewed"}],
                             "state": "reviewed",
                             "failure_reason": None, "stopped": None}


def test_advance_on_an_unknown_task_is_404_and_queues_nothing(client):
    assert client.post("/tasks/nope/advance").status_code == 404
    assert client.get("/jobs").json() == {"jobs": []}


def test_get_unknown_job_is_404_with_the_error_shape(client):
    response = client.get("/jobs/nope")

    assert response.status_code == 404
    assert response.json()["error"] == "unknown job"


def test_abandon_runs_as_a_job_and_fails_the_task_as_abandoned(client, app, ctx):
    ctx.store.save(Task(id="a", prompt="", state=TaskState.PLANNED))

    response = client.post("/tasks/a/abandon")

    assert response.status_code == 202
    app.state.runner.wait_idle()
    job = client.get(f"/jobs/{response.json()['job']['id']}").json()
    assert (job["state"], job["kind"], job["result"]) == ("succeeded", "abandon", {"state": "failed", "failure_reason": "abandoned"})
    assert ctx.store.load("a").state is TaskState.FAILED


def test_an_abandon_the_domain_refuses_ends_as_a_failed_job_with_the_reason(client, app, ctx):
    ctx.store.save(Task(id="a", prompt="", state=TaskState.FAILED))

    response = client.post("/tasks/a/abandon")

    app.state.runner.wait_idle()
    job = client.get(f"/jobs/{response.json()['job']['id']}").json()
    assert (job["state"], job["error"]) == ("failed", "abandon refused: a is failed, already finished")
