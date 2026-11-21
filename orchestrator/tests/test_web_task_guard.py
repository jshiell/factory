import pytest

from factory.sqlite_db import transaction
from webhelpers import add, assert_ui_security_headers

pytest_plugins = ["webfixtures"]

PAGE = "/ui/tasks/20260102-030405"


@pytest.fixture
def task(ctx):
    add(ctx)
    with transaction(ctx.settings.db_path) as conn:
        conn.execute("INSERT INTO tasks (id, state, data) VALUES ('broken-one', 'queued', 'not json')")


def test_guard_the_task_page_without_a_session_is_401(client, task):
    client.cookies.clear()

    assert client.get(PAGE).status_code == 401
    assert client.get("/ui/tasks/nothing-here").status_code == 401


def test_guard_a_foreign_origin_is_403_on_the_task_page(client, task):
    assert client.get(PAGE, headers={"Origin": "http://evil.example"}).status_code == 403


def test_guard_a_non_loopback_host_is_refused_on_the_task_page(client, task):
    assert client.get(PAGE, headers={"Host": "evil.example"}).status_code == 403


@pytest.mark.parametrize("path, status", [(PAGE, 200), ("/ui/tasks/nothing-here", 404), ("/ui/tasks/broken-one", 500)])
def test_guard_the_task_page_404_and_unreadable_page_carry_the_security_headers(client, task, path, status):
    response = client.get(path)

    assert response.status_code == status
    assert_ui_security_headers(response)
