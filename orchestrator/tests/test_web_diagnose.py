import pytest

from factory.task import TaskState
from test_web_reject_ids import REFUSED_IDS, _assert_refusal_without_the_id
from webhelpers import add
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
FORM = "application/x-www-form-urlencoded"


def _post_diagnose(client, task_id=TASK_ID):
    return client.post(f"/ui/repos/proj/tasks/{task_id}/diagnose", content=b"", follow_redirects=False,
                       headers={**same_origin_headers(), "Content-Type": FORM})


def test_guard_posting_a_diagnose_queues_the_job_runs_the_diagnose_stage_and_redirects(client, ctx):
    # guard (green on arrival): the route was registered with the confirm page
    task = add(ctx, TASK_ID, TaskState.FAILED, goal="fix the widget", worktree="/some/worktree")
    ctx.stages["diagnose"] = lambda task_id: ctx.store.load(task_id)

    response = _post_diagnose(client)

    assert response.status_code == 303
    assert response.headers["location"] == f"/ui/repos/proj/tasks/{TASK_ID}"
    client.app.state.runner.wait_idle()
    jobs = client.app.state.repos["proj"].jobs.list()
    assert [(job.kind, job.state) for job in jobs] == [("diagnose", "succeeded")]
    assert ctx.store.load(TASK_ID) == task


@pytest.mark.parametrize("state", [TaskState.QUEUED, TaskState.MERGED])
def test_guard_posting_a_diagnose_for_a_task_that_has_not_failed_is_a_409_and_queues_no_job(client, ctx, state):
    task = add(ctx, TASK_ID, state, goal="fix the widget", worktree="/some/worktree")

    response = _post_diagnose(client)

    assert response.status_code == 409
    assert "cannot be diagnosed" in response.text
    assert client.app.state.repos["proj"].jobs.list() == []
    assert ctx.store.load(TASK_ID) == task


def test_guard_posting_a_diagnose_for_a_task_with_an_unfinished_job_queues_no_second_job(client, ctx):
    add(ctx, TASK_ID, TaskState.FAILED, goal="fix the widget", worktree="/some/worktree")
    first = client.app.state.repos["proj"].jobs.create("diagnose", TASK_ID)

    response = _post_diagnose(client)

    assert response.status_code == 409
    assert "already has a job queued or running" in response.text
    assert client.app.state.repos["proj"].jobs.list() == [first]


@pytest.mark.parametrize("task_id", REFUSED_IDS)
def test_guard_diagnose_for_a_missing_or_invalid_id_is_a_404_without_the_id(client, task_id):
    _assert_refusal_without_the_id(client.get(f"/ui/repos/proj/tasks/{task_id}/diagnose"), 404, task_id)
    _assert_refusal_without_the_id(_post_diagnose(client, task_id), 404, task_id)
    assert client.app.state.repos["proj"].jobs.list() == []
