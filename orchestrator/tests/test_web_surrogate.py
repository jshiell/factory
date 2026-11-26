"""A lone surrogate in a stored goal is shown escaped, and no page breaks on it."""
from factory.task import TaskState
from factory.web.routes import _task_fragment, render_repos
from webhelpers import add

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
GOAL = "bad\ud800goal"
SHOWN = "bad\\ud800goal"


def _assert_escaped(html: str) -> None:
    assert "\ud800" not in html
    assert SHOWN in html


def test_guard_the_list_page_and_fragment_show_a_surrogate_goal_escaped(client, ctx):
    add(ctx, TASK_ID, goal=GOAL)

    response = client.get("/ui/")

    assert response.status_code == 200
    _assert_escaped(response.text)
    _assert_escaped(render_repos(client.app.state.registry))


def test_guard_the_detail_page_and_fragment_show_a_surrogate_goal_escaped(client, ctx):
    add(ctx, TASK_ID, goal=GOAL)

    response = client.get(f"/ui/repos/proj/tasks/{TASK_ID}")

    assert response.status_code == 200
    _assert_escaped(response.text)
    _assert_escaped(_task_fragment(ctx.store, client.app.state.repos["proj"].jobs, TASK_ID, "/ui/repos/proj"))


def test_guard_the_reject_page_shows_a_surrogate_goal_escaped(client, ctx):
    add(ctx, TASK_ID, TaskState.REVIEWED, goal=GOAL)

    response = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/reject")

    assert response.status_code == 200
    _assert_escaped(response.text)
