from urllib.parse import unquote

import pytest

from factory.sqlite_db import transaction
from factory.store import TaskNotFound
from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

REFUSED_IDS = ["20260102-999999", "a.b", "a%20b", "%3Cscript%3E"]


def _plant_unreadable_task(ctx):
    with transaction(ctx.settings.db_path) as conn:
        conn.execute("INSERT INTO tasks (id, state, data) VALUES ('broken-one', 'reviewed', 'not json')")


def _assert_refusal_without_the_id(response, status, task_id):
    assert response.status_code == status
    assert response.headers["content-type"].startswith("text/html")
    assert_ui_security_headers(response)
    assert '<a href="/ui/">' in response.text
    for echoed in (task_id, unquote(task_id), "<script>", "Traceback", "not json", "JSONDecodeError"):
        assert echoed not in response.text


@pytest.mark.parametrize("task_id", REFUSED_IDS)
def test_the_confirm_page_of_a_missing_or_invalid_id_is_a_404_without_the_id(client, task_id):
    _assert_refusal_without_the_id(client.get(f"/ui/tasks/{task_id}/reject"), 404, task_id)


def test_the_confirm_page_of_an_unreadable_task_is_a_500_without_the_id(client, ctx):
    _plant_unreadable_task(ctx)

    _assert_refusal_without_the_id(client.get("/ui/tasks/broken-one/reject"), 500, "broken-one")


def _post_reject(client, task_id):
    return client.post(f"/ui/tasks/{task_id}/reject", headers=same_origin_headers(), follow_redirects=False)


@pytest.mark.parametrize("task_id", REFUSED_IDS)
def test_posting_a_reject_for_a_missing_or_invalid_id_is_a_404_without_the_id(client, task_id):
    _assert_refusal_without_the_id(_post_reject(client, task_id), 404, task_id)


def test_posting_a_reject_for_an_unreadable_task_is_a_500_without_the_id(client, ctx):
    _plant_unreadable_task(ctx)

    _assert_refusal_without_the_id(_post_reject(client, "broken-one"), 500, "broken-one")


class _FailsOnSecondLoad:
    """The first load (the route's own) works; the next, inside reject_plan, raises `error`; saves are recorded."""

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


@pytest.mark.parametrize("error, status", [(TaskNotFound("20260102-030405"), 404), (ValueError("20260102-030405 broken"), 500)])
def test_a_task_lost_after_the_first_load_gives_the_same_page_and_saves_nothing(client, ctx, error, status):
    add(ctx, "20260102-030405", TaskState.REVIEWED)
    client.app.state.store = store = _FailsOnSecondLoad(ctx.store, error)

    _assert_refusal_without_the_id(_post_reject(client, "20260102-030405"), status, "20260102-030405")

    assert store.loads == 2 and store.saved == []
