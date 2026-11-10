from dataclasses import replace
from datetime import datetime

import pytest
from factory.cli import Context
from factory.digest import plan_digest
from factory.rebase_task import Rebased
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore
from factory.task import Task, TaskState
from serving import served


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


@pytest.fixture
def app(client):
    return client.app


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


def test_recover_marks_a_running_task_interrupted_and_records_the_assumed_cost(client, app, ctx):
    ctx.store.save(Task(id="a", prompt="", state=TaskState.RUNNING))

    response = client.post("/tasks/a/recover")

    app.state.runner.wait_idle()
    job = client.get(f"/jobs/{response.json()['job']['id']}").json()
    assert (job["state"], job["result"]) == ("succeeded", {"state": "failed", "failure_reason": "interrupted"})
    assert ctx.ledger.entries()[0]["assumed"] is True


def test_a_recover_the_domain_refuses_ends_as_a_failed_job_with_the_reason(client, app, ctx):
    ctx.store.save(Task(id="a", prompt="", state=TaskState.PLANNED))

    response = client.post("/tasks/a/recover")

    app.state.runner.wait_idle()
    job = client.get(f"/jobs/{response.json()['job']['id']}").json()
    assert (job["state"], job["error"]) == ("failed", "recover refused: a is planned, not running")


def test_rebase_on_a_task_in_the_wrong_state_ends_as_a_failed_job_with_the_reason(client, app, ctx):
    ctx.store.save(Task(id="a", prompt="", state=TaskState.PLANNED))

    response = client.post("/tasks/a/rebase")

    app.state.runner.wait_idle()
    job = client.get(f"/jobs/{response.json()['job']['id']}").json()
    assert job["state"] == "failed"
    assert job["error"].startswith("rebase refused: ")


def test_rebase_reports_the_new_state_and_the_old_audited_commit(client, app, ctx, monkeypatch):
    task = Task(id="a", prompt="", state=TaskState.AGENT_DONE)
    ctx.store.save(task)
    monkeypatch.setattr("factory.server.rebase_task", lambda *args: Rebased(task, "abc123"))

    response = client.post("/tasks/a/rebase")

    app.state.runner.wait_idle()
    job = client.get(f"/jobs/{response.json()['job']['id']}").json()
    assert job["result"] == {"state": "agent_done", "failure_reason": None, "old_audited_sha": "abc123"}


def reviewed(ctx, plan="1. do it", findings=("[required] add a test", "nice to have")):
    task = Task(id="a", prompt="", state=TaskState.REVIEWED, goal="g", plan=plan, plan_digest=plan_digest(plan),
                plan_risks=("r",), review_findings=findings)
    ctx.store.save(task)
    return task


def test_approval_preview_shows_the_plan_notes_and_the_digest_to_echo_back(client, ctx):
    task = reviewed(ctx)

    response = client.get("/tasks/a/approval")

    assert response.status_code == 200
    body = response.json()
    assert (body["goal"], body["plan"], body["risks"]) == ("g", "1. do it", ["r"])
    assert body["plan_digest"] == plan_digest(task.plan)
    assert body["review_findings"] == list(task.review_findings)


def test_approval_preview_of_a_task_not_reviewed_is_409(client, ctx):
    ctx.store.save(Task(id="a", prompt="", state=TaskState.PLANNED))

    assert client.get("/tasks/a/approval").status_code == 409


def test_approve_with_the_shown_digest_queues_the_task(client, ctx):
    task = reviewed(ctx)

    response = client.post("/tasks/a/approve", json={"plan_digest": plan_digest(task.plan)})

    assert response.status_code == 200
    assert response.json() == {"id": "a", "state": "queued"}
    assert ctx.store.load("a").state is TaskState.QUEUED


def test_approve_with_a_stale_digest_is_409_and_approves_nothing(client, ctx):
    reviewed(ctx)

    response = client.post("/tasks/a/approve", json={"plan_digest": plan_digest("another plan")})

    assert response.status_code == 409
    assert response.json()["error"] == "plan changed"
    assert ctx.store.load("a").state is TaskState.REVIEWED


def test_approve_without_a_digest_is_422(client, ctx):
    reviewed(ctx)

    assert client.post("/tasks/a/approve", json={}).status_code == 422


def test_approve_waiving_a_note_that_is_not_required_is_422_and_approves_nothing(client, ctx):
    task = reviewed(ctx)

    response = client.post("/tasks/a/approve", json={"plan_digest": plan_digest(task.plan), "waive": ["nice to have"]})

    assert response.status_code == 422
    assert ctx.store.load("a").state is TaskState.REVIEWED


def test_reject_fails_a_reviewed_task_and_a_task_not_reviewed_is_409(client, ctx):
    reviewed(ctx)

    assert client.post("/tasks/a/reject").status_code == 200
    assert ctx.store.load("a").failure_reason == "plan_rejected_by_human"
    assert client.post("/tasks/a/reject").status_code == 409


def test_retry_of_a_failed_task_creates_a_new_proposed_task_with_its_goal(client, ctx):
    ctx.store.save(Task(id="old", prompt="", state=TaskState.FAILED, goal="add multiply"))

    response = client.post("/tasks/old/retry")

    assert response.status_code == 201
    new = ctx.store.load(response.json()["id"])
    assert (new.state, new.goal, new.retry_of) == (TaskState.PROPOSED, "add multiply", "old")


def test_retry_of_a_task_that_is_not_failed_or_audited_is_409_and_creates_nothing(client, ctx):
    ctx.store.save(Task(id="old", prompt="", state=TaskState.PROPOSED, goal="g"))

    response = client.post("/tasks/old/retry")

    assert response.status_code == 409
    assert response.json()["error"] == "retry refused"
    assert [t.id for t in ctx.store.list().tasks] == ["old"]


def test_retry_of_an_unknown_task_is_404(client):
    assert client.post("/tasks/nope/retry").status_code == 404


def _keepable(ctx, **changes):
    task = Task(id="old", prompt="approved prompt", state=TaskState.FAILED, goal="g", plan="the plan", plan_digest=plan_digest("the plan"),
                plan_risks=("r1",), review_findings=("RECOMMEND (required): fix x", "nit"), failure_reason="audit_rejected",
                audit_findings=("gap one",))
    task = replace(task, **changes)
    ctx.store.save(task)
    return task


def test_retry_preview_shows_what_a_kept_plan_retry_would_send(client, ctx):
    _keepable(ctx)

    body = client.get("/tasks/old/retry-preview").json()

    assert body["goal"] == "g" and body["plan"] == "the plan" and body["risks"] == ["r1"]
    assert body["plan_digest"] == plan_digest("the plan")
    assert body["review_findings"] == ["RECOMMEND (required): fix x", "nit"] and body["required"]
    assert body["audit_gaps"] == ["gap one"] and body["failure_reason"] == "audit_rejected"
    assert body["waived_findings"] == []


def test_retry_preview_of_a_task_with_no_plan_is_409(client, ctx):
    _keepable(ctx, plan=None, plan_digest=None)

    assert client.get("/tasks/old/retry-preview").status_code == 409


def test_retry_keeping_the_plan_queues_a_new_task_with_that_plan(client, ctx):
    _keepable(ctx)

    response = client.post("/tasks/old/retry", json={"keep_plan": True, "plan_digest": plan_digest("the plan")})

    assert response.status_code == 201
    new = ctx.store.load(response.json()["id"])
    assert (new.state, new.plan, new.retry_of) == (TaskState.QUEUED, "the plan", "old")
    assert new.prior_audit_findings == ("gap one",)


def test_retry_keeping_the_plan_with_a_stale_digest_is_409_and_creates_nothing(client, ctx):
    _keepable(ctx)

    response = client.post("/tasks/old/retry", json={"keep_plan": True, "plan_digest": "stale"})

    assert response.status_code == 409 and response.json()["error"] == "retry refused"
    assert [t.id for t in ctx.store.list().tasks] == ["old"]


def test_retry_keeping_the_plan_can_waive_a_required_note(client, ctx):
    _keepable(ctx)

    response = client.post("/tasks/old/retry", json={
        "keep_plan": True, "plan_digest": plan_digest("the plan"), "waive": ["RECOMMEND (required): fix x"]})

    new = ctx.store.load(response.json()["id"])
    assert new.waived_findings == ("RECOMMEND (required): fix x",) and "RECOMMEND (required): fix x" not in new.review_findings


def test_retry_keeping_the_plan_with_an_invalid_waiver_is_422(client, ctx):
    _keepable(ctx)

    response = client.post("/tasks/old/retry", json={
        "keep_plan": True, "plan_digest": plan_digest("the plan"), "waive": ["not a note"]})

    assert response.status_code == 422 and response.json()["error"] == "invalid waiver"
    assert [t.id for t in ctx.store.list().tasks] == ["old"]


def test_retry_keeping_the_plan_can_reinstate_a_waived_note(client, ctx):
    _keepable(ctx, review_findings=("nit",), waived_findings=("RECOMMEND (required): fix x",))

    response = client.post("/tasks/old/retry", json={
        "keep_plan": True, "plan_digest": plan_digest("the plan"), "reinstate": ["RECOMMEND (required): fix x"]})

    new = ctx.store.load(response.json()["id"])
    assert "RECOMMEND (required): fix x" in new.review_findings and new.waived_findings == ()


# --- C10: a long job does not block the quick endpoints, and a queued job re-checks its task ----------------

import threading


@pytest.fixture
def blocked(client):
    """A running job that holds the single worker until released."""
    started, release = threading.Event(), threading.Event()

    def hold():
        started.set()
        release.wait(5)
        return {}

    client.app.state.runner.submit("advance", None, hold)
    assert started.wait(5)
    yield release
    release.set()
    client.app.state.runner.wait_idle()


def test_quick_endpoints_answer_while_a_job_is_running(client, ctx, blocked):
    assert client.post("/tasks", json={"goal": "g"}).status_code == 201
    assert client.get("/tasks").status_code == 200
    assert client.get("/jobs").status_code == 200
    ctx.store.save(Task(id="r", prompt="", state=TaskState.REVIEWED, goal="g", plan="p", plan_digest=plan_digest("p")))
    assert client.post("/tasks/r/reject").status_code == 200


def test_a_queued_job_whose_task_changed_meanwhile_ends_failed_and_changes_nothing(client, ctx, blocked):
    ctx.store.save(Task(id="t", prompt="", state=TaskState.RUNNING, worktree="w"))
    job_id = client.post("/tasks/t/recover").json()["job"]["id"]
    merged = Task(id="t", prompt="", state=TaskState.MERGED)
    ctx.store.save(merged)

    blocked.set()
    client.app.state.runner.wait_idle()

    job = client.get(f"/jobs/{job_id}").json()
    assert job["state"] == "failed" and job["error"] == "recover refused: t is merged, not running"
    assert ctx.store.load("t") == merged


def test_approval_preview_warns_how_many_required_notes_the_cap_dropped(client, ctx):
    from factory.review_notes import MAX_ITEMS
    reviewed(ctx, findings=tuple(f"gap {i}. Recommend (required): handle it" for i in range(MAX_ITEMS + 3)))

    warning = client.get("/tasks/a/approval").json()["warning"]

    assert warning.startswith(f"WARNING: 3 required note(s) beyond the first {MAX_ITEMS} were dropped")
