import json

import pytest

from factory.digest import plan_digest
from factory.store import TaskNotFound
from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
PLAN = "1. add the thing"


class _FailsOnSecondLoad:
    """The first load (the route's own) works; the next, inside service.approve, raises `error`; saves are recorded."""

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

    def update(self, task_id, change):  # plan 77: the actions call update; same load and save path as before
        updated = change(self.load(task_id))
        self.save(updated)
        return updated


@pytest.mark.parametrize("error, status, shown", [
    (TaskNotFound(TASK_ID), 404, "does not exist"),
    (ValueError(f"{TASK_ID} broken"), 409, "plan changed"),  # service.approve turns a ValueError into PlanChanged (plan 76 D6 a)
    (KeyError("broken"), 500, "is unreadable"),  # the other errors `load` lets escape for a bad row
])
def test_a_task_lost_after_the_first_load_gives_the_404_or_plan_changed_page_and_saves_nothing(client, ctx, error, status, shown):
    add(ctx, TASK_ID, TaskState.REVIEWED, goal="fix the widget", plan=PLAN, plan_digest=plan_digest(PLAN))
    client.app.state.store = store = _FailsOnSecondLoad(ctx.store, error)

    response = client.post(f"/ui/tasks/{TASK_ID}/approve", data={"plan_digest": plan_digest(PLAN)},
                           headers=same_origin_headers(), follow_redirects=False)

    assert response.status_code == status
    assert response.headers["content-type"].startswith("text/html")
    assert_ui_security_headers(response)
    assert '<a href="/ui/">' in response.text
    assert shown in response.text
    for echoed in (TASK_ID, "broken", "fix the widget", "Traceback"):
        assert echoed not in response.text
    with pytest.raises(json.JSONDecodeError):
        json.loads(response.text)
    assert store.loads == 2 and store.saved == []
