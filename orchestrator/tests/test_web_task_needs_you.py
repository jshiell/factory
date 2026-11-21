import pytest

from factory.task import TaskState
from webhelpers import add

pytest_plugins = ["webfixtures"]

HUMAN_GATES = {TaskState.OPEN_QUESTIONS, TaskState.REVIEWED, TaskState.AUDITED}


@pytest.mark.parametrize("state", list(TaskState))
def test_the_needs_you_mark_is_present_at_human_gates_only(client, ctx, state):
    add(ctx, state=state)

    page = client.get("/ui/tasks/20260102-030405").text

    assert ("needs you" in page) == (state in HUMAN_GATES)
