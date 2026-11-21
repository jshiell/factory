from urllib.parse import unquote

import pytest

from factory.sqlite_db import transaction
from webhelpers import assert_ui_security_headers

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
