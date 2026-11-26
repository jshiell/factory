import pytest

from factory.task import TaskState
from factory.web.routes import _render_task
from webhelpers import add

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
REJECT_LINK = f'<a href="/ui/repos/proj/tasks/{TASK_ID}/reject">'


def _fragment(ctx, state):
    add(ctx, TASK_ID, state, goal="fix the widget")
    return _render_task(ctx.store.load(TASK_ID), base="/ui/repos/proj")


def test_a_reviewed_tasks_fragment_links_to_the_reject_confirm_page(ctx):
    assert REJECT_LINK in _fragment(ctx, TaskState.REVIEWED)


@pytest.mark.parametrize("state", [state for state in TaskState if state is not TaskState.REVIEWED])
def test_no_other_states_fragment_links_to_it(ctx, state):
    assert "/reject" not in _fragment(ctx, state)


@pytest.mark.parametrize("state", list(TaskState))
def test_a_fragment_never_holds_a_form_or_inline_script_or_style(ctx, state):
    fragment = _fragment(ctx, state)

    assert "<form" not in fragment and "<script" not in fragment and "<style" not in fragment and "style=" not in fragment
