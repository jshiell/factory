from urllib.parse import unquote

import pytest

from factory.task import TaskState
from webhelpers import add

pytest_plugins = ["webfixtures"]


def test_the_page_shows_id_state_goal_or_prompt_and_failure_reason(client, ctx):
    add(ctx, "20260102-030405", TaskState.FAILED, goal="fix the widget", failure_reason="gate failed")
    add(ctx, "20260102-030406", goal=None, prompt="the raw prompt")

    response = client.get("/ui/tasks/20260102-030405")

    assert response.status_code == 200
    for shown in ("20260102-030405", "failed", "fix the widget", "gate failed"):
        assert shown in response.text
    assert "the raw prompt" in client.get("/ui/tasks/20260102-030406").text


@pytest.mark.parametrize("task_id", ["20260102-999999", "a.b", "a%20b", "%3Cscript%3E"])
def test_a_missing_or_invalid_id_gives_a_404_in_the_layout_without_the_id(client, task_id):
    response = client.get(f"/ui/tasks/{task_id}")

    assert response.status_code == 404
    assert '<link rel="stylesheet" href="/ui/static/ui.css">' in response.text
    assert '<a href="/ui/">' in response.text
    for echoed in (task_id, unquote(task_id), "<script>"):
        assert echoed not in response.text
