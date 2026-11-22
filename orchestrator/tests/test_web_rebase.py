import pytest

from factory.task import TaskState
from test_web_abandon_bad_input import BAD_POSTS
from test_web_reject_ids import REFUSED_IDS, _assert_refusal_without_the_id
from webhelpers import add, assert_ui_security_headers
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
FORM = "application/x-www-form-urlencoded"


def _post_rebase(client, task_id=TASK_ID, body=b"", content_type=FORM, headers=None):
    base = same_origin_headers() if headers is None else headers
    return client.post(f"/ui/tasks/{task_id}/rebase", content=body, follow_redirects=False,
                       headers={**base, "Content-Type": content_type})


def test_posting_a_rebase_queues_a_rebase_job_and_redirects_to_the_task_page(client, ctx):
    add(ctx, TASK_ID, TaskState.AUDITED, goal="fix the widget")

    response = _post_rebase(client)

    assert response.status_code == 303
    assert response.headers["location"] == f"/ui/tasks/{TASK_ID}"
    job = client.app.state.jobs.latest_for_task(TASK_ID)
    assert job.kind == "rebase"


def test_posting_a_rebase_for_a_task_that_is_not_audited_is_a_409_and_queues_no_job(client, ctx):
    task = add(ctx, TASK_ID, TaskState.VERIFIED, goal="fix the widget")

    response = _post_rebase(client)

    assert response.status_code == 409
    assert "cannot be rebased" in response.text
    assert client.app.state.jobs.list() == []
    assert ctx.store.load(TASK_ID) == task


def test_posting_a_rebase_for_a_task_with_an_unfinished_job_is_a_409_and_queues_no_second_job(client, ctx):
    add(ctx, TASK_ID, TaskState.AUDITED, goal="fix the widget")
    first = client.app.state.jobs.create("advance", TASK_ID)

    response = _post_rebase(client)

    assert response.status_code == 409
    assert "already has a job queued or running" in response.text
    assert client.app.state.jobs.list() == [first]


@pytest.mark.parametrize("task_id", REFUSED_IDS)
def test_rebase_for_a_missing_or_invalid_id_is_a_404_without_the_id(client, task_id):
    _assert_refusal_without_the_id(client.get(f"/ui/tasks/{task_id}/rebase"), 404, task_id)
    _assert_refusal_without_the_id(_post_rebase(client, task_id), 404, task_id)
    assert client.app.state.jobs.list() == []


@pytest.mark.parametrize("body, content_type", [BAD_POSTS["text/plain"], BAD_POSTS["body over the cap"]],
                         ids=["text/plain", "body over the cap"])
def test_a_bad_rebase_form_is_a_422_that_queues_nothing(client, ctx, body, content_type):
    before = add(ctx, TASK_ID, TaskState.AUDITED, goal="fix the widget")

    response = _post_rebase(client, body=body.encode(), content_type=content_type)

    assert response.status_code == 422
    assert client.app.state.jobs.list() == []
    assert ctx.store.load(TASK_ID) == before


def test_the_rebase_page_and_post_without_a_session_or_from_a_foreign_origin_are_refused_and_queue_nothing(client, ctx):
    before = add(ctx, TASK_ID, TaskState.AUDITED, goal="fix the widget")
    foreign = _post_rebase(client, headers={"Origin": "http://evil.example"})
    client.cookies.clear()

    responses = [client.get(f"/ui/tasks/{TASK_ID}/rebase"), _post_rebase(client)]

    assert foreign.status_code == 403
    assert [response.status_code for response in responses] == [401, 401]
    for response in [foreign, *responses]:
        assert_ui_security_headers(response)
    assert client.app.state.jobs.list() == []
    assert ctx.store.load(TASK_ID) == before
