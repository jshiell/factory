import pytest

from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
NOT_ABANDONABLE = {"merged": {"state": TaskState.MERGED}, "failed": {"state": TaskState.FAILED},
                   "running": {"state": TaskState.RUNNING}, "fix in flight": {"state": TaskState.AGENT_DONE, "fix_in_flight": True}}
ABANDONABLE = [state for state in TaskState if state not in (TaskState.MERGED, TaskState.FAILED, TaskState.RUNNING)]


@pytest.mark.parametrize("fields", NOT_ABANDONABLE.values(), ids=NOT_ABANDONABLE.keys())
def test_the_abandon_page_of_a_task_that_cannot_be_abandoned_is_a_409_without_form_or_id(client, ctx, fields):
    task = add(ctx, TASK_ID, goal="fix the widget", **fields)

    response = client.get(f"/ui/tasks/{TASK_ID}/abandon")

    assert response.status_code == 409
    assert_ui_security_headers(response)
    assert '<link rel="stylesheet" href="/ui/static/ui.css">' in response.text
    assert "cannot be abandoned" in response.text
    assert '<a href="/ui/">' in response.text
    assert "<form" not in response.text
    for echoed in (TASK_ID, "fix the widget"):
        assert echoed not in response.text
    assert ctx.store.load(TASK_ID) == task


@pytest.mark.parametrize("state", ABANDONABLE)
def test_the_abandon_page_of_every_other_state_is_served(client, ctx, state):
    add(ctx, TASK_ID, state, goal="fix the widget")

    assert client.get(f"/ui/tasks/{TASK_ID}/abandon").status_code == 200
