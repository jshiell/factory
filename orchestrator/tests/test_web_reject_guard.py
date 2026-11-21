import pytest

from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
REJECT_PATH = f"/ui/tasks/{TASK_ID}/reject"


@pytest.fixture
def reviewed(ctx):
    return add(ctx, TASK_ID, TaskState.REVIEWED, goal="fix the widget")


def _post(client, headers=None, path=REJECT_PATH):
    return client.post(path, headers=same_origin_headers() if headers is None else headers, follow_redirects=False)


def test_a_reject_post_without_a_session_is_401_and_changes_nothing(client, ctx, reviewed):
    client.cookies.clear()

    response = _post(client)

    assert response.status_code == 401
    assert_ui_security_headers(response)
    assert ctx.store.load(TASK_ID) == reviewed


@pytest.mark.parametrize("headers", [
    {"Origin": "http://evil.example"},
    {"Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "cross-site"},
    {"Origin": "null", "Sec-Fetch-Site": "cross-site"},
    {},
])
def test_a_reject_post_with_a_foreign_or_unvouched_origin_is_403_and_changes_nothing(client, ctx, reviewed, headers):
    response = _post(client, headers)

    assert response.status_code == 403
    assert_ui_security_headers(response)
    assert ctx.store.load(TASK_ID) == reviewed


def test_a_reject_post_to_a_non_loopback_host_is_403_and_changes_nothing(client, ctx, reviewed):
    response = _post(client, {"Host": "evil.example", "Origin": "http://evil.example"})

    assert response.status_code == 403
    assert_ui_security_headers(response)
    assert ctx.store.load(TASK_ID) == reviewed


def test_a_null_origin_vouched_as_same_origin_rejects_the_task(client, ctx, reviewed):
    response = _post(client, {"Origin": "null", "Sec-Fetch-Site": "same-origin"})

    assert response.status_code == 303


def test_the_security_headers_are_on_the_confirm_page_the_redirect_and_the_refusals(client, ctx):
    add(ctx, TASK_ID, TaskState.REVIEWED)
    add(ctx, "20260102-030406", TaskState.QUEUED)

    responses = [
        client.get(REJECT_PATH),
        _post(client),
        _post(client, path="/ui/tasks/20260102-030406/reject"),
        _post(client, path="/ui/tasks/20260102-999999/reject"),
    ]

    assert [response.status_code for response in responses] == [200, 303, 409, 404]
    for response in responses:
        assert_ui_security_headers(response)


def test_head_on_the_reject_path_is_a_read_that_changes_nothing(client, ctx, reviewed):
    """Starlette serves HEAD from the GET route (the confirm page), so HEAD never reaches the POST handler."""
    response = client.head(REJECT_PATH)

    assert response.status_code == 200
    assert response.content == b""
    assert_ui_security_headers(response)
    assert ctx.store.load(TASK_ID) == reviewed
