from dataclasses import replace
from urllib.parse import urlencode

import pytest

from factory.task import TaskState
from gitutil import git
from webposts import same_origin_headers

pytest_plugins = ["webfixtures", "webmerge"]

TASK_ID = "20260102-030405"
FORM = "application/x-www-form-urlencoded"


def post_merge(client, body=b"", task_id=TASK_ID, content_type=FORM, headers=None):
    base = same_origin_headers() if headers is None else headers
    return client.post(f"/ui/repos/proj/tasks/{task_id}/merge", content=body, follow_redirects=False,
                       headers={**base, "Content-Type": content_type})


def sha_form(sha: str) -> bytes:
    return urlencode({"audited_sha": sha}).encode()


def test_posting_the_shown_sha_queues_a_merge_job_redirects_and_the_job_fast_forwards_main(client, ctx, audited, repo):
    response = post_merge(client, sha_form(audited.audited_sha))

    assert response.status_code == 303
    assert response.headers["location"] == f"/ui/repos/proj/tasks/{TASK_ID}"
    job = client.app.state.repos["proj"].jobs.latest_for_task(TASK_ID)
    assert job.kind == "merge"
    client.app.state.runner.wait_idle()
    assert client.app.state.repos["proj"].jobs.get(job.id).state == "succeeded"
    assert ctx.store.load(TASK_ID).state is TaskState.MERGED
    assert git(repo, "rev-parse", "HEAD") == audited.audited_sha


NOT_MERGEABLE = [state for state in TaskState if state is not TaskState.AUDITED]


@pytest.mark.parametrize("state", NOT_MERGEABLE)
def test_posting_the_audited_sha_for_a_task_that_is_not_audited_is_a_409_and_queues_nothing(client, ctx, audited, repo, state):
    task = replace(audited, state=state)
    ctx.store.save(task)

    response = post_merge(client, sha_form(audited.audited_sha))

    assert response.status_code == 409
    assert "cannot be merged" in response.text
    assert client.app.state.repos["proj"].jobs.list() == []
    assert ctx.store.load(TASK_ID) == task
    assert git(repo, "rev-parse", "HEAD") == audited.base_sha


def test_posting_the_audited_sha_for_a_task_with_a_fix_agent_in_flight_is_a_409_and_queues_nothing(client, ctx, audited):
    ctx.store.save(replace(audited, fix_in_flight=True))

    response = post_merge(client, sha_form(audited.audited_sha))

    assert response.status_code == 409
    assert "cannot be merged in its current state" in response.text
    assert client.app.state.repos["proj"].jobs.list() == []


def test_a_bad_form_posted_for_a_task_that_is_not_audited_gets_the_wrong_state_409(client, ctx, audited):
    ctx.store.save(replace(audited, state=TaskState.VERIFIED))

    response = post_merge(client, b"not a form", content_type="text/plain")

    assert response.status_code == 409
    assert "cannot be merged" in response.text


@pytest.mark.parametrize("started", [False, True], ids=["queued", "running"])
def test_posting_a_merge_for_a_task_with_an_unfinished_job_is_a_409_and_queues_no_second_job(client, audited, started):
    first = client.app.state.repos["proj"].jobs.create("merge", TASK_ID)
    if started:
        client.app.state.repos["proj"].jobs.mark_running(first.id)

    response = post_merge(client, sha_form(audited.audited_sha))

    assert response.status_code == 409
    assert "already has a job queued or running" in response.text
    assert [job.id for job in client.app.state.repos["proj"].jobs.list()] == [first.id]
