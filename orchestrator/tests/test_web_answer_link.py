import pytest

from factory.task import TaskState
from factory.web.routes import _render_task
from webhelpers import add

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
ANSWER_LINK = f'<a href="/ui/tasks/{TASK_ID}/answer">'


def _fragment(ctx, state):
    add(ctx, TASK_ID, state, goal="fix the widget")
    return _render_task(ctx.store.load(TASK_ID))


def test_an_open_questions_fragment_links_to_the_answer_page(ctx):
    assert ANSWER_LINK in _fragment(ctx, TaskState.OPEN_QUESTIONS)


@pytest.mark.parametrize("state", [state for state in TaskState if state is not TaskState.OPEN_QUESTIONS])
def test_no_other_states_fragment_links_to_it(ctx, state):
    assert "/answer" not in _fragment(ctx, state)
