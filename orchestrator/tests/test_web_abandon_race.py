from dataclasses import replace

from factory.task import TaskState
from factory.web import routes
from test_web_abandon import _post_abandon
from test_web_abandon_busy import _assert_busy_page
from webhelpers import add

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"


def test_a_job_queued_while_the_body_is_read_still_refuses_the_abandon(client, ctx, monkeypatch):
    """The unfinished-job check and the queueing must have no await between them (plan 80 D8)."""
    task = add(ctx, TASK_ID, TaskState.QUEUED, goal="fix the widget")
    first = None
    real_read_form = routes._read_form

    async def read_form_while_another_request_queues_a_job(request):
        nonlocal first
        first = client.app.state.jobs.create("advance", TASK_ID)
        return await real_read_form(request)

    monkeypatch.setattr(routes, "_read_form", read_form_while_another_request_queues_a_job)

    _assert_busy_page(_post_abandon(client))

    assert client.app.state.jobs.list() == [first]
    assert ctx.store.load(TASK_ID) == task


def test_a_task_that_leaves_its_state_while_the_body_is_read_gets_the_wrong_state_page(client, ctx, monkeypatch):
    task = add(ctx, TASK_ID, TaskState.QUEUED, goal="fix the widget")
    real_read_form = routes._read_form

    async def read_form_while_the_task_is_merged(request):
        ctx.store.save(replace(task, state=TaskState.MERGED))
        return await real_read_form(request)

    monkeypatch.setattr(routes, "_read_form", read_form_while_the_task_is_merged)

    response = _post_abandon(client)

    assert response.status_code == 409
    assert "cannot be abandoned" in response.text
    assert client.app.state.jobs.list() == []
