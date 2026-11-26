"""Reading the merge form awaits, so the POST loads the task again and checks it with no await before the queueing."""
from dataclasses import replace

import pytest

from factory.sqlite_db import transaction
from factory.store import TaskNotFound
from factory.task import TaskState
from factory.web import routes
from gitutil import git
from test_web_merge import TASK_ID, post_merge, sha_form

pytest_plugins = ["webfixtures", "webmerge"]


def _while_the_body_is_read(monkeypatch, change):
    real_read_form = routes._read_form

    async def read_form_while_something_else_happens(request):
        change()
        return await real_read_form(request)

    monkeypatch.setattr(routes, "_read_form", read_form_while_something_else_happens)


def _assert_nothing_merged(client, repo, audited, jobs=()):
    assert [job.id for job in client.app.state.repos["proj"].jobs.list()] == list(jobs)
    client.app.state.runner.wait_idle()
    assert git(repo, "rev-parse", "HEAD") == audited.base_sha


def test_a_job_queued_while_the_body_is_read_still_refuses_the_merge(client, audited, repo, monkeypatch):
    queued = []
    _while_the_body_is_read(monkeypatch, lambda: queued.append(client.app.state.repos["proj"].jobs.create("advance", TASK_ID)))

    response = post_merge(client, sha_form(audited.audited_sha))

    assert response.status_code == 409
    assert "already has a job queued or running" in response.text
    _assert_nothing_merged(client, repo, audited, [queued[0].id])


@pytest.mark.parametrize("change, shown", [
    (lambda task: replace(task, state=TaskState.VERIFIED), "cannot be merged in its current state"),
    (lambda task: replace(task, state=TaskState.MERGED), "cannot be merged in its current state"),
    (lambda task: replace(task, fix_in_flight=True), "cannot be merged in its current state"),
    (lambda task: replace(task, audited_sha="0" * 40), "changed since you opened this page"),
], ids=["left audited, same sha", "merged elsewhere", "fix agent started", "audited sha changed"])
def test_a_task_changed_while_the_body_is_read_is_refused_and_nothing_is_queued(
        client, ctx, audited, repo, monkeypatch, change, shown):
    changed = change(audited)
    _while_the_body_is_read(monkeypatch, lambda: ctx.store.save(changed))

    response = post_merge(client, sha_form(audited.audited_sha))

    assert response.status_code == 409
    assert shown in response.text
    _assert_nothing_merged(client, repo, audited)
    assert ctx.store.load(TASK_ID) == changed


def _execute(ctx, sql):
    with transaction(ctx.settings.db_path) as conn:
        conn.execute(sql, (TASK_ID,))


@pytest.mark.parametrize("sql, status", [
    ("DELETE FROM tasks WHERE id = ?", 404),
    ("UPDATE tasks SET data = 'not json' WHERE id = ?", 500),
], ids=["deleted", "unreadable"])
def test_a_task_gone_or_broken_while_the_body_is_read_gets_the_task_refusal_page_and_nothing_is_queued(
        client, ctx, audited, repo, monkeypatch, sql, status):
    _while_the_body_is_read(monkeypatch, lambda: _execute(ctx, sql))

    response = post_merge(client, sha_form(audited.audited_sha))

    assert response.status_code == status
    assert response.headers["content-type"].startswith("text/html")
    for leaked in (TASK_ID, "not json", "Traceback"):
        assert leaked not in response.text
    _assert_nothing_merged(client, repo, audited)


@pytest.mark.parametrize("error, status", [(TaskNotFound(TASK_ID), 404), (ValueError("not json"), 500)],
                         ids=["gone", "unreadable"])
def test_a_task_that_goes_or_breaks_just_before_the_enqueue_loads_it_queues_nothing(
        client, ctx, audited, repo, monkeypatch, error, status):
    """Another process can still write between the route's second load and the one inside enqueue_merge."""
    real_load, loads = ctx.store.load, []

    def load_failing_the_third_time(task_id):
        loads.append(task_id)
        if len(loads) == 3:
            raise error
        return real_load(task_id)
    monkeypatch.setattr(ctx.store, "load", load_failing_the_third_time)

    response = post_merge(client, sha_form(audited.audited_sha))

    assert len(loads) == 3
    assert response.status_code == status
    assert response.headers["content-type"].startswith("text/html")
    for leaked in (TASK_ID, "not json", "Traceback"):
        assert leaked not in response.text
    _assert_nothing_merged(client, repo, audited)
