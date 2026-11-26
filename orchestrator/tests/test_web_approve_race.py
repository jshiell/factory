from dataclasses import replace

from factory.digest import plan_digest
from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
PLAN = "1. add the thing"


class _MovesOnSecondLoad:
    """The first load (the route's own) sees the task as it was; before the next, inside service.approve, another
    writer has moved it to `state`. Saves through this wrapper are recorded."""

    def __init__(self, store, state):
        self._store, self._state, self.loads, self.saved = store, state, 0, []

    def load(self, task_id):
        self.loads += 1
        if self.loads == 2:
            self._store.save(replace(self._store.load(task_id), state=self._state))
        return self._store.load(task_id)

    def save(self, task):
        self.saved.append(task)
        self._store.save(task)

    def update(self, task_id, change):  # plan 77: the actions call update; same load and save path as before
        updated = change(self.load(task_id))
        self.save(updated)
        return updated


def test_a_task_that_left_reviewed_after_the_first_load_gives_the_409_page_and_saves_nothing(client, ctx):
    add(ctx, TASK_ID, TaskState.REVIEWED, goal="fix the widget", plan=PLAN, plan_digest=plan_digest(PLAN))
    client.app.state.repos["proj"].store = store = _MovesOnSecondLoad(ctx.store, TaskState.FAILED)

    response = client.post(f"/ui/repos/proj/tasks/{TASK_ID}/approve", data={"plan_digest": plan_digest(PLAN)},
                           headers=same_origin_headers(), follow_redirects=False)

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("text/html")
    assert_ui_security_headers(response)
    assert "cannot be approved" in response.text
    for echoed in (TASK_ID, "fix the widget", "Traceback"):
        assert echoed not in response.text
    assert store.saved == []
    assert ctx.store.load(TASK_ID).state is TaskState.FAILED
