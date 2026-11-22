import re

import pytest

from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
ADVANCEABLE = [TaskState.PROPOSED, TaskState.PLANNED, TaskState.QUEUED, TaskState.AGENT_DONE, TaskState.VERIFIED]
NOT_ADVANCEABLE = [state for state in TaskState if state not in ADVANCEABLE]


def test_the_advance_page_names_the_goal_escaped_warns_about_spend_and_posts_to_the_same_path(client, ctx):
    add(ctx, TASK_ID, TaskState.QUEUED, goal="fix <b>the</b> widget")

    response = client.get(f"/ui/tasks/{TASK_ID}/advance")

    assert response.status_code == 200
    assert_ui_security_headers(response)
    assert "fix &lt;b&gt;the&lt;/b&gt; widget" in response.text and "<b>" not in response.text
    assert "next decision point" in response.text
    assert "spend money" in response.text and "10 steps" in response.text
    forms = re.findall(r"<form\b[^>]*>.*?</form>", response.text, re.S)
    assert len(forms) == 1
    assert f'action="/ui/tasks/{TASK_ID}/advance"' in forms[0]
    assert len(re.findall(r"<(?:input|button|select|textarea)\b", forms[0])) == 1


@pytest.mark.parametrize("state", ADVANCEABLE)
def test_the_advance_page_of_every_advanceable_state_is_served(client, ctx, state):
    add(ctx, TASK_ID, state, goal="fix the widget")

    assert client.get(f"/ui/tasks/{TASK_ID}/advance").status_code == 200


@pytest.mark.parametrize("state", NOT_ADVANCEABLE)
def test_the_advance_page_of_a_task_that_cannot_be_advanced_is_a_409_without_form_or_id(client, ctx, state):
    task = add(ctx, TASK_ID, state, goal="fix the widget")

    response = client.get(f"/ui/tasks/{TASK_ID}/advance")

    assert response.status_code == 409
    assert_ui_security_headers(response)
    assert "cannot be advanced" in response.text
    assert "<form" not in response.text
    for echoed in (TASK_ID, "fix the widget"):
        assert echoed not in response.text
    assert ctx.store.load(TASK_ID) == task


def test_the_advance_page_of_a_task_with_an_unfinished_job_is_a_409_busy_page(client, ctx):
    add(ctx, TASK_ID, TaskState.QUEUED, goal="fix the widget")
    client.app.state.jobs.create("advance", TASK_ID)

    response = client.get(f"/ui/tasks/{TASK_ID}/advance")

    assert response.status_code == 409
    assert "already has a job queued or running" in response.text
    assert "<form" not in response.text
