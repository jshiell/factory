import pytest

from factory.abandon_task import can_abandon
from factory.advance import can_advance
from factory.merge_task import can_merge
from factory.rebase_task import can_rebase
from factory.recover_task import can_recover
from factory.retry_task import can_retry
from factory.task import TaskState
from factory.web.routes import _task_fragment
from test_web_task_job import job_in_state
from webhelpers import add

pytest_plugins = ["webfixtures"]

TASK = "20260102-030405"
PAGE = f"/ui/repos/proj/tasks/{TASK}"
PREDICATES = {"abandon": can_abandon, "recover": can_recover, "advance": can_advance, "rebase": can_rebase,
              "merge": can_merge, "retry": can_retry}


def link(action):
    return f'<a href="/ui/repos/proj/tasks/{TASK}/{action}">'


def shown(html):
    return {action for action in PREDICATES if link(action) in html}


def expected(task):
    return {action for action, allowed in PREDICATES.items() if allowed(task)}


@pytest.mark.parametrize("state", list(TaskState))
def test_the_task_page_links_exactly_the_actions_valid_for_its_state(client, ctx, state):
    task = add(ctx, TASK, state, goal="fix the widget")

    assert shown(client.get(PAGE).text) == expected(task)


@pytest.mark.parametrize("state", list(TaskState))
def test_the_task_fragment_links_exactly_the_actions_valid_for_its_state(client, ctx, state):
    task = add(ctx, TASK, state, goal="fix the widget")

    assert shown(_task_fragment(ctx.store, client.app.state.repos["proj"].jobs, TASK, "/ui/repos/proj")) == expected(task)


def test_abandon_is_hidden_while_a_fix_agent_is_in_flight(client, ctx):
    add(ctx, TASK, TaskState.AUDITED, goal="g", fix_in_flight=True)

    assert shown(client.get(PAGE).text) <= {"retry"}


@pytest.mark.parametrize("state", ["queued", "running"])
def test_no_gated_action_is_offered_while_the_task_has_an_unfinished_job(client, ctx, state):
    add(ctx, TASK, TaskState.RUNNING, goal="g")
    job_in_state(client, state)

    assert shown(client.get(PAGE).text) == set()


def test_recover_is_offered_again_once_the_job_has_finished(client, ctx):
    add(ctx, TASK, TaskState.RUNNING, goal="g")
    job_in_state(client, "failed")

    assert link("recover") in client.get(PAGE).text


def test_a_failed_task_offers_retry_but_not_abandon(client, ctx):
    add(ctx, TASK, TaskState.FAILED, goal="g")

    assert shown(client.get(PAGE).text) == {"retry"}


def test_the_action_links_have_readable_text(client, ctx):
    add(ctx, TASK, TaskState.AUDITED, goal="g")

    html = client.get(PAGE).text

    for text in ("Abandon this task", "Rebase this task", "Merge this task"):
        assert text in html
