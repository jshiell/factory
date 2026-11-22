import pytest

from factory.task import TaskState
from test_web_abandon import FORM
from webhelpers import add, assert_ui_security_headers
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
ABANDON_PATH = f"/ui/tasks/{TASK_ID}/abandon"


@pytest.fixture
def queued_task(ctx):
    return add(ctx, TASK_ID, TaskState.QUEUED, goal="fix the widget")


def _post(client, headers=None):
    base = same_origin_headers() if headers is None else headers
    return client.post(ABANDON_PATH, content=b"", headers={**base, "Content-Type": FORM}, follow_redirects=False)


def test_the_abandon_page_and_post_without_a_session_are_401_and_queue_nothing(client, ctx, queued_task):
    client.cookies.clear()

    responses = [client.get(ABANDON_PATH), _post(client)]

    assert [response.status_code for response in responses] == [401, 401]
    for response in responses:
        assert_ui_security_headers(response)
    assert client.app.state.jobs.list() == []
    assert ctx.store.load(TASK_ID) == queued_task


@pytest.mark.parametrize("headers", [
    {"Origin": "http://evil.example"},
    {"Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "cross-site"},
    {"Origin": "null", "Sec-Fetch-Site": "cross-site"},
    {},
    {"Host": "evil.example", "Origin": "http://evil.example"},
], ids=["foreign-origin", "cross-site", "null-cross-site", "no-origin", "non-loopback-host"])
def test_an_abandon_post_with_a_foreign_or_unvouched_origin_is_403_and_queues_nothing(client, ctx, queued_task, headers):
    response = _post(client, headers)

    assert response.status_code == 403
    assert_ui_security_headers(response)
    assert client.app.state.jobs.list() == []
    assert ctx.store.load(TASK_ID) == queued_task


def test_the_abandon_page_on_a_non_loopback_host_or_with_a_cross_site_fetch_is_403(client, queued_task):
    responses = [client.get(ABANDON_PATH, headers={"Host": "evil.example"}),
                 client.get(ABANDON_PATH, headers={"Sec-Fetch-Site": "cross-site"})]

    assert [response.status_code for response in responses] == [403, 403]


def test_the_security_headers_are_on_the_page_the_redirect_and_every_refusal(client, ctx):
    add(ctx, TASK_ID, TaskState.QUEUED)
    add(ctx, "20260102-030406", TaskState.MERGED)

    responses = [client.get(ABANDON_PATH), _post(client), _post(client), client.get("/ui/tasks/20260102-030406/abandon"),
                 client.get("/ui/tasks/20260102-999999/abandon")]

    assert [response.status_code for response in responses] == [200, 303, 409, 409, 404]
    for response in responses:
        assert_ui_security_headers(response)
