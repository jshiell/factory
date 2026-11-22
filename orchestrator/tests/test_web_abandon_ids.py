import pytest

from factory.store import TaskNotFound
from factory.task import TaskState
from test_web_abandon import _post_abandon
from test_web_reject_ids import (
    REFUSED_IDS,
    _assert_refusal_without_the_id,
    _plant_unreadable_task,
)
from webhelpers import add

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"


@pytest.mark.parametrize("task_id", REFUSED_IDS)
def test_the_abandon_page_of_a_missing_or_invalid_id_is_a_404_without_the_id(client, task_id):
    _assert_refusal_without_the_id(client.get(f"/ui/tasks/{task_id}/abandon"), 404, task_id)


def test_the_abandon_page_of_an_unreadable_task_is_a_500_without_the_id(client, ctx):
    _plant_unreadable_task(ctx)

    _assert_refusal_without_the_id(client.get("/ui/tasks/broken-one/abandon"), 500, "broken-one")


@pytest.mark.parametrize("task_id", REFUSED_IDS)
def test_posting_an_abandon_for_a_missing_or_invalid_id_is_a_404_without_the_id(client, task_id):
    _assert_refusal_without_the_id(_post_abandon(client, task_id), 404, task_id)
    assert client.app.state.jobs.list() == []


def test_posting_an_abandon_for_an_unreadable_task_is_a_500_without_the_id(client, ctx):
    _plant_unreadable_task(ctx)

    _assert_refusal_without_the_id(_post_abandon(client, "broken-one"), 500, "broken-one")
    assert client.app.state.jobs.list() == []


class _FailsOnSecondLoad:
    """The first load (the route's own) works; the next, inside the action's enqueue, raises `error`; saves are recorded."""

    def __init__(self, store, error):
        self._store, self._error, self.loads, self.saved = store, error, 0, []

    def load(self, task_id):
        self.loads += 1
        if self.loads > 1:
            raise self._error
        return self._store.load(task_id)

    def save(self, task):
        self.saved.append(task)
        self._store.save(task)


@pytest.mark.parametrize("error, status", [(TaskNotFound(TASK_ID), 404), (ValueError(f"{TASK_ID} broken"), 500),
                                           (KeyError("broken"), 500)])
def test_a_task_lost_after_the_first_load_gives_the_same_page_and_queues_no_job(client, ctx, error, status):
    add(ctx, TASK_ID, TaskState.QUEUED)
    client.app.state.store = client.app.state.actions._ctx.store = store = _FailsOnSecondLoad(ctx.store, error)

    _assert_refusal_without_the_id(_post_abandon(client, TASK_ID), status, TASK_ID)

    assert store.saved == []
    assert client.app.state.jobs.list() == []
