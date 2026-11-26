import pytest

from factory.digest import plan_digest
from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
APPROVE_PATH = f"/ui/repos/proj/tasks/{TASK_ID}/approve"
PLAN = "1. add the thing"
FORM = {"plan_digest": plan_digest(PLAN)}


@pytest.fixture
def reviewed_task(ctx):
    return add(ctx, TASK_ID, TaskState.REVIEWED, goal="fix the widget", plan=PLAN, plan_digest=plan_digest(PLAN))


def _post(client, headers=None, path=APPROVE_PATH, data=FORM):
    return client.post(path, data=data, headers=same_origin_headers() if headers is None else headers, follow_redirects=False)


def test_the_approve_page_and_post_without_a_session_are_401_and_change_nothing(client, ctx, reviewed_task):
    client.cookies.clear()

    responses = [client.get(APPROVE_PATH), _post(client)]

    assert [response.status_code for response in responses] == [401, 401]
    for response in responses:
        assert_ui_security_headers(response)
    assert ctx.store.load(TASK_ID) == reviewed_task


@pytest.mark.parametrize("headers", [
    {"Origin": "http://evil.example"},
    {"Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "cross-site"},
    {"Origin": "null", "Sec-Fetch-Site": "cross-site"},
    {},
], ids=["foreign-origin", "cross-site", "null-cross-site", "no-origin"])
def test_an_approve_post_with_a_foreign_or_unvouched_origin_is_403_and_changes_nothing(client, ctx, reviewed_task, headers):
    response = _post(client, headers)

    assert response.status_code == 403
    assert_ui_security_headers(response)
    assert ctx.store.load(TASK_ID) == reviewed_task


def test_an_approve_post_to_a_non_loopback_host_is_403_and_changes_nothing(client, ctx, reviewed_task):
    response = _post(client, {"Host": "evil.example", "Origin": "http://evil.example"})

    assert response.status_code == 403
    assert_ui_security_headers(response)
    assert ctx.store.load(TASK_ID) == reviewed_task


def test_the_approve_page_on_a_non_loopback_host_or_with_a_cross_site_fetch_is_403(client, reviewed_task):
    responses = [client.get(APPROVE_PATH, headers={"Host": "evil.example"}),
                 client.get(APPROVE_PATH, headers={"Sec-Fetch-Site": "cross-site"})]

    assert [response.status_code for response in responses] == [403, 403]
    for response in responses:
        assert_ui_security_headers(response)


def test_the_approve_page_without_an_origin_is_served(client, reviewed_task):
    response = client.get(APPROVE_PATH)

    assert response.status_code == 200


def test_the_security_headers_are_on_the_page_the_redirect_and_every_refusal(client, ctx):
    add(ctx, TASK_ID, TaskState.REVIEWED, plan=PLAN, plan_digest=plan_digest(PLAN))
    add(ctx, "20260102-030406", TaskState.QUEUED)

    responses = [
        client.get(APPROVE_PATH),
        _post(client, data={"plan_digest": plan_digest("1. another plan")}),
        _post(client, data={}),
        _post(client),
        _post(client, path="/ui/repos/proj/tasks/20260102-030406/approve"),
        _post(client, path="/ui/repos/proj/tasks/20260102-999999/approve"),
    ]

    assert [response.status_code for response in responses] == [200, 409, 422, 303, 409, 404]
    for response in responses:
        assert_ui_security_headers(response)
