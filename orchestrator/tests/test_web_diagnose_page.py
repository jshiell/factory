import re

import pytest

from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
NOT_FAILED = [state for state in TaskState if state is not TaskState.FAILED]


def test_the_diagnose_page_names_the_goal_escaped_states_the_cost_and_posts_to_the_same_path(client, ctx):
    add(ctx, TASK_ID, TaskState.FAILED, goal="fix <b>the</b> widget", worktree="/some/worktree")

    response = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/diagnose")

    assert response.status_code == 200
    assert_ui_security_headers(response)
    assert "fix &lt;b&gt;the&lt;/b&gt; widget" in response.text and "<b>" not in response.text
    assert "read-only" in response.text and "spending cap" in response.text
    forms = re.findall(r"<form\b[^>]*>.*?</form>", response.text, re.S)
    assert len(forms) == 1
    assert f'action="/ui/repos/proj/tasks/{TASK_ID}/diagnose"' in forms[0]
    assert len(re.findall(r"<(?:input|button|select|textarea)\b", forms[0])) == 1


@pytest.mark.parametrize("state", NOT_FAILED)
def test_the_diagnose_page_of_a_task_that_has_not_failed_is_a_409_without_form_or_id(client, ctx, state):
    task = add(ctx, TASK_ID, state, goal="fix the widget", worktree="/some/worktree")

    response = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/diagnose")

    assert response.status_code == 409
    assert_ui_security_headers(response)
    assert "cannot be diagnosed" in response.text
    assert "<form" not in response.text
    for echoed in (TASK_ID, "fix the widget"):
        assert echoed not in response.text
    assert ctx.store.load(TASK_ID) == task


def test_the_diagnose_page_of_a_failed_task_without_a_worktree_is_a_409(client, ctx):
    add(ctx, TASK_ID, TaskState.FAILED, goal="fix the widget", worktree=None)

    response = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/diagnose")

    assert response.status_code == 409
    assert "<form" not in response.text


def test_the_diagnose_page_of_a_task_with_an_unfinished_job_is_a_409_busy_page(client, ctx):
    add(ctx, TASK_ID, TaskState.FAILED, goal="fix the widget", worktree="/some/worktree")
    client.app.state.repos["proj"].jobs.create("diagnose", TASK_ID)

    response = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/diagnose")

    assert response.status_code == 409
    assert "already has a job queued or running" in response.text
    assert "<form" not in response.text
