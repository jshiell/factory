from urllib.parse import urlencode

import pytest

from factory.digest import plan_digest
from factory.task import TaskState
from test_web_abandon_bad_input import BAD_POSTS
from test_web_reject_ids import (
    REFUSED_IDS,
    _assert_refusal_without_the_id,
    _plant_unreadable_task,
)
from test_web_retry import keep_form, post_retry, task_ids
from test_web_retry_page import TASK_ID, keepable
from webhelpers import assert_ui_security_headers

pytest_plugins = ["webfixtures"]

DIGEST = plan_digest("the plan")


@pytest.mark.parametrize("task_id", REFUSED_IDS)
def test_retry_for_a_missing_or_invalid_id_is_a_404_without_the_id(client, ctx, task_id):
    _assert_refusal_without_the_id(client.get(f"/ui/repos/proj/tasks/{task_id}/retry"), 404, task_id)
    _assert_refusal_without_the_id(post_retry(client, task_id=task_id), 404, task_id)
    _assert_refusal_without_the_id(post_retry(client, keep_form(DIGEST), task_id), 404, task_id)
    assert task_ids(ctx) == []


def test_retry_for_an_unreadable_task_is_a_500_without_the_id(client, ctx):
    _plant_unreadable_task(ctx)

    _assert_refusal_without_the_id(client.get("/ui/repos/proj/tasks/broken-one/retry"), 500, "broken-one")
    _assert_refusal_without_the_id(post_retry(client, task_id="broken-one"), 500, "broken-one")
    assert task_ids(ctx) == []


def _assert_bad_form_and_nothing_created(response, ctx, before):
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert_ui_security_headers(response)
    assert "<form" not in response.text
    for echoed in (TASK_ID, DIGEST, "Traceback"):
        assert echoed not in response.text
    assert task_ids(ctx) == [TASK_ID]
    assert ctx.store.load(TASK_ID) == before


@pytest.mark.parametrize("body, content_type", BAD_POSTS.values(), ids=BAD_POSTS.keys())
def test_a_bad_retry_form_is_a_422_that_creates_nothing(client, ctx, body, content_type):
    before = keepable(ctx)

    response = post_retry(client, body.encode(), content_type=content_type or "")  # "" stands for no content type

    _assert_bad_form_and_nothing_created(response, ctx, before)


BAD_KEEP_FIELDS = {
    "digest missing": b"keep_plan=1",
    "digest blank": b"keep_plan=1&plan_digest=",
    "digest repeated, same value": f"keep_plan=1&plan_digest={DIGEST}&plan_digest={DIGEST}".encode(),
    "digest repeated, other first": f"keep_plan=1&plan_digest=x&plan_digest={DIGEST}".encode(),
    "keep_plan repeated": f"keep_plan=1&keep_plan=1&plan_digest={DIGEST}".encode(),
    "blank keep_plan, no digest": b"keep_plan=",
}


@pytest.mark.parametrize("body", BAD_KEEP_FIELDS.values(), ids=BAD_KEEP_FIELDS.keys())
def test_a_missing_blank_or_repeated_keep_plan_field_is_a_422_that_creates_nothing(client, ctx, body):
    before = keepable(ctx)

    _assert_bad_form_and_nothing_created(post_retry(client, body), ctx, before)


def test_a_digest_without_keep_plan_is_a_retry_from_scratch(client, ctx):
    keepable(ctx)

    response = post_retry(client, urlencode({"plan_digest": DIGEST, "other": "x"}).encode())

    assert response.status_code == 303
    (new_id,) = [task_id for task_id in task_ids(ctx) if task_id != TASK_ID]
    assert ctx.store.load(new_id).state is TaskState.PROPOSED


@pytest.mark.parametrize("body", [b"", keep_form(DIGEST)], ids=["plain", "keep plan"])
def test_retry_accepts_duplicates_and_ignores_an_unfinished_job(client, ctx, body):
    """D8 does not apply to retry: each post makes a new task, even while the task has a job running."""
    keepable(ctx)
    job = client.app.state.repos["proj"].jobs.create("advance", TASK_ID)
    client.app.state.repos["proj"].jobs.mark_running(job.id)

    first, second = post_retry(client, body), post_retry(client, body)

    assert [first.status_code, second.status_code] == [303, 303]
    assert first.headers["location"] != second.headers["location"]
    assert len(task_ids(ctx)) == 3
    assert [j.id for j in client.app.state.repos["proj"].jobs.list()] == [job.id]


def test_the_retry_page_and_post_without_a_session_or_from_a_foreign_origin_are_refused_and_create_nothing(client, ctx):
    before = keepable(ctx)
    foreign = post_retry(client, keep_form(DIGEST), headers={"Origin": "http://evil.example"})
    no_origin = post_retry(client, headers={})
    client.cookies.clear()

    responses = [client.get(f"/ui/repos/proj/tasks/{TASK_ID}/retry"), post_retry(client), post_retry(client, keep_form(DIGEST))]

    assert [foreign.status_code, no_origin.status_code] == [403, 403]
    assert [response.status_code for response in responses] == [401, 401, 401]
    for response in [foreign, no_origin, *responses]:
        assert_ui_security_headers(response)
        assert DIGEST not in response.text and "the plan" not in response.text
    assert task_ids(ctx) == [TASK_ID]
    assert ctx.store.load(TASK_ID) == before
