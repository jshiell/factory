import pytest

from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"


@pytest.mark.parametrize("fields", [{"round": "0", "q0": "postgres"}, {"q0": "postgres"}, {"round": "x"}], ids=["valid", "no-round", "bad-round"])
@pytest.mark.parametrize("state", [state for state in TaskState if state is not TaskState.OPEN_QUESTIONS])
def test_posting_an_answer_for_a_task_without_open_questions_is_a_409_and_saves_nothing(client, ctx, state, fields):
    task = add(ctx, TASK_ID, state, goal="fix the widget")

    response = client.post(f"/ui/tasks/{TASK_ID}/answer", data=fields, headers=same_origin_headers(), follow_redirects=False)

    assert response.status_code == 409
    assert_ui_security_headers(response)
    assert "no open questions" in response.text
    for echoed in (TASK_ID, "fix the widget", "postgres"):
        assert echoed not in response.text
    assert ctx.store.load(TASK_ID) == task
