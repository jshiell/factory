from urllib.parse import unquote

import pytest

from factory.sqlite_db import transaction
from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers

pytest_plugins = ["webfixtures"]


def test_the_page_shows_id_state_goal_or_prompt_and_failure_reason(client, ctx):
    add(ctx, "20260102-030405", TaskState.FAILED, goal="fix the widget", failure_reason="gate failed")
    add(ctx, "20260102-030406", goal=None, prompt="the raw prompt")

    response = client.get("/ui/tasks/20260102-030405")

    assert response.status_code == 200
    for shown in ("20260102-030405", "failed", "fix the widget", "gate failed"):
        assert shown in response.text
    assert "the raw prompt" in client.get("/ui/tasks/20260102-030406").text


@pytest.mark.parametrize("task_id", ["20260102-999999", "a.b", "a%20b", "%3Cscript%3E"])
def test_a_missing_or_invalid_id_gives_a_404_in_the_layout_without_the_id(client, task_id):
    response = client.get(f"/ui/tasks/{task_id}")

    assert response.status_code == 404
    assert '<link rel="stylesheet" href="/ui/static/ui.css">' in response.text
    assert '<a href="/ui/">' in response.text
    for echoed in (task_id, unquote(task_id), "<script>"):
        assert echoed not in response.text


def test_an_unreadable_task_gives_an_html_unreadable_page_without_traceback_id_or_raw_data(client, ctx):
    with transaction(ctx.settings.db_path) as conn:
        conn.execute("INSERT INTO tasks (id, state, data) VALUES ('broken-one', 'queued', 'not json')")

    response = client.get("/ui/tasks/broken-one")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("text/html")
    assert "unreadable" in response.text
    assert '<a href="/ui/">' in response.text
    for leaked in ("Traceback", "not json", "broken-one", "JSONDecodeError"):
        assert leaked not in response.text


@pytest.mark.parametrize("task_id", ["a%2fb", "..%2fsecret", "a%2Fb"])
def test_guard_an_encoded_slash_id_gives_a_404_without_the_id_and_with_the_security_headers(client, task_id):
    response = client.get(f"/ui/tasks/{task_id}")

    assert response.status_code == 404
    assert "secret" not in response.text and "a/b" not in response.text and "%2" not in response.text
    assert_ui_security_headers(response)
