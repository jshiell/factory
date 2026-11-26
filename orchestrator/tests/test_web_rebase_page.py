import re

import pytest

from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
NOT_REBASABLE = [state for state in TaskState if state is not TaskState.AUDITED]


def test_the_rebase_page_names_the_goal_escaped_says_what_rebase_does_and_posts_to_the_same_path(client, ctx):
    add(ctx, TASK_ID, TaskState.AUDITED, goal="fix <b>the</b> widget")

    response = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/rebase")

    assert response.status_code == 200
    assert_ui_security_headers(response)
    assert "fix &lt;b&gt;the&lt;/b&gt; widget" in response.text and "<b>" not in response.text
    assert "current main" in response.text and "agent done" in response.text
    assert "verified and audited again" in response.text and "audit result" in response.text
    forms = re.findall(r"<form\b[^>]*>.*?</form>", response.text, re.S)
    assert len(forms) == 1
    assert f'action="/ui/repos/proj/tasks/{TASK_ID}/rebase"' in forms[0]
    assert len(re.findall(r"<(?:input|button|select|textarea)\b", forms[0])) == 1


@pytest.mark.parametrize("state", NOT_REBASABLE)
def test_the_rebase_page_of_a_task_that_is_not_audited_is_a_409_without_form_or_id(client, ctx, state):
    task = add(ctx, TASK_ID, state, goal="fix the widget")

    response = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/rebase")

    assert response.status_code == 409
    assert_ui_security_headers(response)
    assert "cannot be rebased" in response.text
    assert "<form" not in response.text
    for echoed in (TASK_ID, "fix the widget"):
        assert echoed not in response.text
    assert ctx.store.load(TASK_ID) == task


def test_the_rebase_page_of_an_audited_task_with_an_unfinished_job_is_a_409_busy_page(client, ctx):
    add(ctx, TASK_ID, TaskState.AUDITED, goal="fix the widget")
    job = client.app.state.repos["proj"].jobs.create("advance", TASK_ID)
    client.app.state.repos["proj"].jobs.mark_running(job.id)

    response = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/rebase")

    assert response.status_code == 409
    assert "already has a job queued or running" in response.text
    assert "<form" not in response.text
