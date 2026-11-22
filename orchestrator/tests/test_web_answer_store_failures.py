import json

import pytest

from factory.store import TaskNotFound
from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
QUESTIONS = ({"number": 1, "title": "which database", "body": "pick one", "recommended": "sqlite"},)


class _FailsOnSecondLoad:
    """The first load (the route's own) works; the next, inside answer_questions, raises `error`; saves are recorded."""

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


def _post_answer(client):
    return client.post(f"/ui/tasks/{TASK_ID}/answer", data={"round": "1", "q0": "postgres"},
                       headers=same_origin_headers(), follow_redirects=False)


@pytest.mark.parametrize("error, status", [(TaskNotFound(TASK_ID), 404), (ValueError(f"{TASK_ID} broken"), 500)])
def test_a_task_lost_after_the_first_load_gives_the_404_or_500_page_and_saves_nothing(client, ctx, error, status):
    add(ctx, TASK_ID, TaskState.OPEN_QUESTIONS, goal="fix the widget", grill_rounds=1, open_questions=QUESTIONS)
    client.app.state.store = store = _FailsOnSecondLoad(ctx.store, error)

    response = _post_answer(client)

    assert response.status_code == status
    assert response.headers["content-type"].startswith("text/html")
    assert_ui_security_headers(response)
    assert '<a href="/ui/">' in response.text
    for echoed in (TASK_ID, "broken", "postgres", "Traceback"):
        assert echoed not in response.text
    with pytest.raises(json.JSONDecodeError):
        json.loads(response.text)
    assert store.loads == 2 and store.saved == []
