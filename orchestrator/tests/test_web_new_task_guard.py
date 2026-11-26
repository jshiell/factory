import pytest

from webhelpers import assert_ui_security_headers
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

FORM = {"Content-Type": "application/x-www-form-urlencoded"}


def _post(client, headers):
    return client.post("/ui/tasks/new", content=b"repo=proj&goal=fix", headers={**FORM, **headers}, follow_redirects=False)


def test_a_new_task_post_without_a_session_is_401_and_creates_nothing(client, ctx):
    client.cookies.clear()

    response = _post(client, same_origin_headers())

    assert response.status_code == 401
    assert_ui_security_headers(response)
    assert ctx.store.list().tasks == []


@pytest.mark.parametrize("headers", [
    {"Origin": "http://evil.example"},
    {"Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "cross-site"},
    {},
])
def test_a_new_task_post_with_a_foreign_or_unvouched_origin_is_403_and_creates_nothing(client, ctx, headers):
    response = _post(client, headers)

    assert response.status_code == 403
    assert_ui_security_headers(response)
    assert ctx.store.list().tasks == []


def test_the_new_task_form_is_not_shadowed_by_the_task_page(client):
    assert "<textarea" in client.get("/ui/tasks/new").text
