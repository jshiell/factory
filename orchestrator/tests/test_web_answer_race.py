from dataclasses import replace

from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
QUESTIONS = ({"number": 1, "title": "which database", "body": "pick one", "recommended": "sqlite"},)


class _MovesOnSecondLoad:
    """The first load (the route's own) sees the task as it was; before the next, inside answer_questions, another
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


def test_a_task_that_left_open_questions_after_the_first_load_gives_the_409_page_and_saves_nothing(client, ctx):
    add(ctx, TASK_ID, TaskState.OPEN_QUESTIONS, goal="fix the widget", grill_rounds=1, open_questions=QUESTIONS)
    client.app.state.store = store = _MovesOnSecondLoad(ctx.store, TaskState.PROPOSED)

    response = client.post(f"/ui/tasks/{TASK_ID}/answer", data={"round": "1", "q0": "postgres"},
                           headers=same_origin_headers(), follow_redirects=False)

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("text/html")
    assert_ui_security_headers(response)
    assert "no open questions" in response.text
    for echoed in (TASK_ID, "fix the widget", "postgres", "Traceback"):
        assert echoed not in response.text
    assert store.loads == 2 and store.saved == []
    assert ctx.store.load(TASK_ID).state is TaskState.PROPOSED
