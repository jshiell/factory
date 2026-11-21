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
