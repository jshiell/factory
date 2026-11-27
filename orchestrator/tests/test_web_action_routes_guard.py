import pytest

from factory.task import TaskState
from test_web_abandon import FORM
from webhelpers import add
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
ACTIONS = ["abandon", "recover", "advance", "diagnose", "rebase", "merge", "retry"]


@pytest.fixture
def task(ctx):
    return add(ctx, TASK_ID, TaskState.AUDITED, goal="fix the widget")


def _post(client, path, headers):
    return client.post(path, content=b"", headers={**headers, "Content-Type": FORM}, follow_redirects=False)


@pytest.mark.parametrize("action", ACTIONS)
def test_an_action_page_and_post_without_a_session_are_401_and_queue_nothing(client, ctx, task, action):
    path = f"/ui/repos/proj/tasks/{TASK_ID}/{action}"
    client.cookies.clear()

    responses = [client.get(path), _post(client, path, same_origin_headers())]

    assert [response.status_code for response in responses] == [401, 401]
    assert client.app.state.repos["proj"].jobs.list() == []
    assert ctx.store.load(TASK_ID) == task


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("headers", [
    {"Origin": "http://evil.example"},
    {"Origin": "http://127.0.0.1:8765", "Sec-Fetch-Site": "cross-site"},
    {},
], ids=["foreign-origin", "cross-site", "no-origin"])
def test_an_action_post_with_a_foreign_or_unvouched_origin_is_403_and_queues_nothing(client, ctx, task, action, headers):
    response = _post(client, f"/ui/repos/proj/tasks/{TASK_ID}/{action}", headers)

    assert response.status_code == 403
    assert client.app.state.repos["proj"].jobs.list() == []
    assert ctx.store.load(TASK_ID) == task
