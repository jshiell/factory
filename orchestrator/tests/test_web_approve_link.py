import pytest

from factory.task import TaskState
from factory.web.routes import _render_task
from webhelpers import add

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
APPROVE_LINK = f'<a href="/ui/repos/proj/tasks/{TASK_ID}/approve">'


def _fragment(ctx, state):
    add(ctx, TASK_ID, state, goal="fix the widget")
    return _render_task(ctx.store.load(TASK_ID), base="/ui/repos/proj")


def test_a_reviewed_fragment_links_to_the_approve_page_beside_the_reject_link(ctx):
    fragment = _fragment(ctx, TaskState.REVIEWED)

    assert APPROVE_LINK in fragment
    assert f'<a href="/ui/repos/proj/tasks/{TASK_ID}/reject">' in fragment


@pytest.mark.parametrize("state", [state for state in TaskState if state is not TaskState.REVIEWED])
def test_no_other_states_fragment_links_to_it(ctx, state):
    assert "/approve" not in _fragment(ctx, state)
