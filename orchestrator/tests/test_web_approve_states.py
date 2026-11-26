import pytest

from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers

pytest_plugins = ["webfixtures"]


@pytest.mark.parametrize("state", [state for state in TaskState if state is not TaskState.REVIEWED])
def test_the_approve_page_of_a_task_not_waiting_for_review_is_a_409_without_form_or_id(client, ctx, state):
    add(ctx, "20260102-030405", state, goal="fix the widget")

    response = client.get("/ui/repos/proj/tasks/20260102-030405/approve")

    assert response.status_code == 409
    assert_ui_security_headers(response)
    assert '<link rel="stylesheet" href="/ui/static/ui.css">' in response.text
    assert "cannot be approved" in response.text
    assert '<a href="/ui/">' in response.text
    assert "<form" not in response.text
    for echoed in ("20260102-030405", "fix the widget"):
        assert echoed not in response.text
    assert ctx.store.load("20260102-030405").state is state
