import pytest

from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
ANSWER_PATH = f"/ui/repos/proj/tasks/{TASK_ID}/answer"
QUESTIONS = ({"number": 1, "title": "which database", "body": "pick one", "recommended": "sqlite"},)
FORM = {"round": "1", "q0": "postgres"}


@pytest.fixture
def open_task(ctx):
    return add(ctx, TASK_ID, TaskState.OPEN_QUESTIONS, goal="fix the widget", grill_rounds=1, open_questions=QUESTIONS)


def _post(client, headers=None, path=ANSWER_PATH, data=FORM):
    return client.post(path, data=data, headers=same_origin_headers() if headers is None else headers, follow_redirects=False)


def test_the_answer_page_and_post_without_a_session_are_401_and_change_nothing(client, ctx, open_task):
    client.cookies.clear()

    responses = [client.get(ANSWER_PATH), _post(client)]

    assert [response.status_code for response in responses] == [401, 401]
    for response in responses:
        assert_ui_security_headers(response)
    assert ctx.store.load(TASK_ID) == open_task


@pytest.mark.parametrize("headers", [
    {"Origin": "http://evil.example"},
    {"Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "cross-site"},
    {"Origin": "null", "Sec-Fetch-Site": "cross-site"},
    {},
], ids=["foreign-origin", "cross-site", "null-cross-site", "no-origin"])
def test_an_answer_post_with_a_foreign_or_unvouched_origin_is_403_and_changes_nothing(client, ctx, open_task, headers):
    response = _post(client, headers)

    assert response.status_code == 403
    assert_ui_security_headers(response)
    assert ctx.store.load(TASK_ID) == open_task


def test_an_answer_post_to_a_non_loopback_host_is_403_and_changes_nothing(client, ctx, open_task):
    response = _post(client, {"Host": "evil.example", "Origin": "http://evil.example"})

    assert response.status_code == 403
    assert_ui_security_headers(response)
    assert ctx.store.load(TASK_ID) == open_task


def test_the_answer_page_on_a_non_loopback_host_or_with_a_cross_site_fetch_is_403(client, open_task):
    responses = [client.get(ANSWER_PATH, headers={"Host": "evil.example"}),
                 client.get(ANSWER_PATH, headers={"Sec-Fetch-Site": "cross-site"})]

    assert [response.status_code for response in responses] == [403, 403]
    for response in responses:
        assert_ui_security_headers(response)


def test_the_answer_page_without_an_origin_is_served(client, open_task):
    response = client.get(ANSWER_PATH)

    assert response.status_code == 200


def test_the_security_headers_are_on_the_page_the_redirect_and_every_refusal(client, ctx):
    add(ctx, TASK_ID, TaskState.OPEN_QUESTIONS, grill_rounds=1, open_questions=QUESTIONS)
    add(ctx, "20260102-030406", TaskState.QUEUED)

    responses = [
        client.get(ANSWER_PATH),
        _post(client, data={"round": "9"}),
        _post(client, data={"round": "x"}),
        _post(client),
        _post(client, path="/ui/repos/proj/tasks/20260102-030406/answer"),
        _post(client, path="/ui/repos/proj/tasks/20260102-999999/answer"),
    ]

    assert [response.status_code for response in responses] == [200, 409, 422, 303, 409, 404]
    for response in responses:
        assert_ui_security_headers(response)
