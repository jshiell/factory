import pytest

from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"


def _assert_busy_page(response):
    assert response.status_code == 409
    assert_ui_security_headers(response)
    assert '<link rel="stylesheet" href="/ui/static/ui.css">' in response.text
    assert "already has a job queued or running" in response.text
    assert "current state" not in response.text
    assert '<a href="/ui/">' in response.text
    assert "<form" not in response.text
    for echoed in (TASK_ID, "fix the widget"):
        assert echoed not in response.text


@pytest.mark.parametrize("started", [False, True], ids=["queued", "running"])
def test_the_abandon_page_of_a_task_with_an_unfinished_job_is_a_409_busy_page(client, ctx, started):
    task = add(ctx, TASK_ID, TaskState.QUEUED, goal="fix the widget")
    job = client.app.state.repos["proj"].jobs.create("advance", TASK_ID)
    if started:
        client.app.state.repos["proj"].jobs.mark_running(job.id)

    _assert_busy_page(client.get(f"/ui/repos/proj/tasks/{TASK_ID}/abandon"))

    assert ctx.store.load(TASK_ID) == task


@pytest.mark.parametrize("finish", ["mark_succeeded", "mark_failed", "interrupted"])
def test_a_finished_job_does_not_block_the_abandon_page(client, ctx, finish):
    add(ctx, TASK_ID, TaskState.QUEUED, goal="fix the widget")
    jobs = client.app.state.repos["proj"].jobs
    job = jobs.create("advance", TASK_ID)
    jobs.mark_running(job.id)
    if finish == "mark_succeeded":
        jobs.mark_succeeded(job.id, {})
    elif finish == "mark_failed":
        jobs.mark_failed(job.id, "boom")
    else:
        jobs.interrupt_unfinished()

    assert client.get(f"/ui/repos/proj/tasks/{TASK_ID}/abandon").status_code == 200
