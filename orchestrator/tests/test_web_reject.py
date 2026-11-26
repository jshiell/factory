from dataclasses import replace

import pytest

from factory.task import TaskState
from webhelpers import add
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]


def test_posting_a_reject_fails_the_task_and_redirects_to_its_page(client, ctx):
    add(ctx, "20260102-030405", TaskState.REVIEWED, goal="fix the widget")

    response = client.post("/ui/repos/proj/tasks/20260102-030405/reject", headers=same_origin_headers(), follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/repos/proj/tasks/20260102-030405"
    task = ctx.store.load("20260102-030405")
    assert task.state is TaskState.FAILED
    assert task.failure_reason == "plan_rejected_by_human"


class _MovesOnSecondLoad:
    """The route's own load sees the task as stored; the task then moves to `queued` before reject_plan loads it."""

    def __init__(self, store):
        self._store, self._loads = store, 0

    def load(self, task_id):
        self._loads += 1
        task = self._store.load(task_id)
        if self._loads == 2:
            task = replace(task, state=TaskState.QUEUED)
            self._store.save(task)
        return task

    def save(self, task):
        self._store.save(task)

    def update(self, task_id, change):  # plan 77: the actions call update; same load and save path as before
        updated = change(self.load(task_id))
        self.save(updated)
        return updated


def test_a_task_that_leaves_review_between_the_load_and_the_rejection_gives_the_409_page(client, ctx):
    add(ctx, "20260102-030405", TaskState.REVIEWED, goal="fix the widget")
    client.app.state.repos["proj"].store = _MovesOnSecondLoad(ctx.store)

    response = client.post("/ui/repos/proj/tasks/20260102-030405/reject", headers=same_origin_headers(), follow_redirects=False)

    assert response.status_code == 409
    assert "20260102-030405" not in response.text
    assert ctx.store.load("20260102-030405").state is TaskState.QUEUED


@pytest.mark.parametrize("state", [state for state in TaskState if state is not TaskState.REVIEWED])
def test_posting_a_reject_for_a_task_not_awaiting_review_is_the_409_page_and_changes_nothing(client, ctx, state):
    before = add(ctx, "20260102-030405", state, goal="fix the widget")

    response = client.post("/ui/repos/proj/tasks/20260102-030405/reject", headers=same_origin_headers(), follow_redirects=False)

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("text/html")
    assert '<a href="/ui/">' in response.text
    assert "20260102-030405" not in response.text and "fix the widget" not in response.text
    assert ctx.store.load("20260102-030405") == before
