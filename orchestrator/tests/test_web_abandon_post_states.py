import pytest

from factory.task import TaskState
from test_web_abandon import _post_abandon
from test_web_abandon_busy import _assert_busy_page
from test_web_abandon_states import NOT_ABANDONABLE
from webhelpers import add, assert_ui_security_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"


@pytest.mark.parametrize("fields", NOT_ABANDONABLE.values(), ids=NOT_ABANDONABLE.keys())
def test_posting_an_abandon_for_a_task_that_cannot_be_abandoned_is_a_409_and_queues_no_job(client, ctx, fields):
    task = add(ctx, TASK_ID, goal="fix the widget", **fields)

    response = _post_abandon(client)

    assert response.status_code == 409
    assert_ui_security_headers(response)
    assert "cannot be abandoned" in response.text
    assert "<form" not in response.text
    for echoed in (TASK_ID, "fix the widget"):
        assert echoed not in response.text
    assert client.app.state.repos["proj"].jobs.list() == []
    assert ctx.store.load(TASK_ID) == task


def test_posting_an_abandon_for_a_task_with_an_unfinished_job_is_a_409_busy_page_and_queues_no_second_job(client, ctx):
    task = add(ctx, TASK_ID, TaskState.QUEUED, goal="fix the widget")
    first = client.app.state.repos["proj"].jobs.create("advance", TASK_ID)

    _assert_busy_page(_post_abandon(client))

    assert client.app.state.repos["proj"].jobs.list() == [first]
    assert ctx.store.load(TASK_ID) == task


def test_a_second_abandon_after_the_first_is_a_409_and_queues_no_second_job(client, ctx):
    add(ctx, TASK_ID, TaskState.QUEUED, goal="fix the widget")
    assert _post_abandon(client).status_code == 303

    response = _post_abandon(client)

    assert response.status_code == 409
    assert [job.kind for job in client.app.state.repos["proj"].jobs.list()] == ["abandon"]
