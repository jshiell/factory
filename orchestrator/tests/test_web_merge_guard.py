import pytest

from gitutil import git
from test_web_abandon_bad_input import BAD_POSTS
from test_web_merge import TASK_ID, post_merge, sha_form
from test_web_reject_ids import (
    REFUSED_IDS,
    _assert_refusal_without_the_id,
    _plant_unreadable_task,
)
from webhelpers import assert_ui_security_headers

pytest_plugins = ["webfixtures", "webmerge"]


@pytest.mark.parametrize("task_id", REFUSED_IDS)
def test_merge_for_a_missing_or_invalid_id_is_a_404_without_the_id(client, task_id):
    _assert_refusal_without_the_id(client.get(f"/ui/tasks/{task_id}/merge"), 404, task_id)
    _assert_refusal_without_the_id(post_merge(client, sha_form("0" * 40), task_id), 404, task_id)
    assert client.app.state.jobs.list() == []


def test_merge_for_an_unreadable_task_is_a_500_without_the_id(client, ctx):
    _plant_unreadable_task(ctx)

    _assert_refusal_without_the_id(client.get("/ui/tasks/broken-one/merge"), 500, "broken-one")
    _assert_refusal_without_the_id(post_merge(client, sha_form("0" * 40), "broken-one"), 500, "broken-one")
    assert client.app.state.jobs.list() == []


@pytest.mark.parametrize("body, content_type", BAD_POSTS.values(), ids=BAD_POSTS.keys())
def test_a_bad_merge_form_is_a_422_that_queues_nothing(client, ctx, audited, repo, body, content_type):
    response = post_merge(client, body.encode(), content_type=content_type or "")  # "" stands for no content type

    assert response.status_code == 422
    assert_ui_security_headers(response)
    assert "<form" not in response.text and TASK_ID not in response.text
    assert client.app.state.jobs.list() == []
    assert ctx.store.load(TASK_ID) == audited
    assert git(repo, "rev-parse", "HEAD") == audited.base_sha


def test_a_bad_merge_form_carrying_the_right_sha_still_queues_nothing(client, ctx, audited):
    padded = sha_form(audited.audited_sha) + b"&padding=" + b"a" * (64 * 1024)

    assert post_merge(client, padded).status_code == 422
    assert post_merge(client, sha_form(audited.audited_sha), content_type="text/plain").status_code == 422
    assert client.app.state.jobs.list() == []


def test_the_merge_page_and_post_without_a_session_or_from_a_foreign_origin_are_refused_and_queue_nothing(
        client, ctx, audited, repo):
    foreign = post_merge(client, sha_form(audited.audited_sha), headers={"Origin": "http://evil.example"})
    no_origin = post_merge(client, sha_form(audited.audited_sha), headers={})
    client.cookies.clear()

    responses = [client.get(f"/ui/tasks/{TASK_ID}/merge"), post_merge(client, sha_form(audited.audited_sha))]

    assert [foreign.status_code, no_origin.status_code] == [403, 403]
    assert [response.status_code for response in responses] == [401, 401]
    for response in [foreign, no_origin, *responses]:
        assert_ui_security_headers(response)
        assert audited.audited_sha not in response.text
    assert client.app.state.jobs.list() == []
    assert ctx.store.load(TASK_ID) == audited
    assert git(repo, "rev-parse", "HEAD") == audited.base_sha
