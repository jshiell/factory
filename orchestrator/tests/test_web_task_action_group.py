import pytest

from factory.task import TaskState
from factory.web.routes import _task_fragment
from test_web_task_actions import PAGE, TASK, expected
from webhelpers import add

pytest_plugins = ["webfixtures"]

GROUP = '<nav class="actions">'
HAS_OWN_LINK = {TaskState.REVIEWED, TaskState.OPEN_QUESTIONS}


@pytest.mark.parametrize("state", list(TaskState))
def test_the_task_page_groups_its_action_links_in_a_nav_only_when_there_are_any(client, ctx, state):
    task = add(ctx, TASK, state, goal="fix the widget")

    assert (GROUP in client.get(PAGE).text) == bool(expected(task) or state in HAS_OWN_LINK)


@pytest.mark.parametrize("state", list(TaskState))
def test_the_task_fragment_groups_its_action_links_the_same_way(client, ctx, state):
    add(ctx, TASK, state, goal="fix the widget")

    assert (GROUP in _task_fragment(ctx.store, client.app.state.repos["proj"].jobs, TASK, "/ui/repos/proj")) == (GROUP in client.get(PAGE).text)
