"""Reading the retry form awaits, so the POST loads the task again and checks it before retrying."""
from dataclasses import replace

import pytest

from factory.digest import plan_digest
from factory.task import TaskState
from test_web_merge_race import _execute, _while_the_body_is_read
from test_web_retry import keep_form, post_retry, task_ids
from test_web_retry_page import TASK_ID, keepable

pytest_plugins = ["webfixtures"]

KEEP = keep_form(plan_digest("the plan"))


@pytest.mark.parametrize("body", [b"", KEEP], ids=["plain", "keep plan"])
@pytest.mark.parametrize("change", [
    lambda task: replace(task, state=TaskState.MERGED),
    lambda task: replace(task, state=TaskState.QUEUED),
    lambda task: replace(task, goal=None),
], ids=["merged", "queued again", "goal gone"])
def test_a_task_that_stops_being_retryable_while_the_body_is_read_gets_the_wrong_state_page(
        client, ctx, monkeypatch, body, change):
    changed = change(keepable(ctx))
    _while_the_body_is_read(monkeypatch, lambda: ctx.store.save(changed))

    response = post_retry(client, body)

    assert response.status_code == 409
    assert "cannot be retried in its current state" in response.text
    assert task_ids(ctx) == [TASK_ID]


def test_a_plan_changed_while_the_body_is_read_gets_the_stale_page(client, ctx, monkeypatch):
    changed = replace(keepable(ctx), plan="a new plan", plan_digest=plan_digest("a new plan"))
    _while_the_body_is_read(monkeypatch, lambda: ctx.store.save(changed))

    response = post_retry(client, KEEP)

    assert response.status_code == 409
    assert "changed since you opened this page" in response.text
    assert task_ids(ctx) == [TASK_ID]


@pytest.mark.parametrize("body", [b"", KEEP], ids=["plain", "keep plan"])
@pytest.mark.parametrize("sql, status", [
    ("DELETE FROM tasks WHERE id = ?", 404),
    ("UPDATE tasks SET data = 'not json' WHERE id = ?", 500),
], ids=["deleted", "unreadable"])
def test_a_task_gone_or_broken_while_the_body_is_read_gets_the_task_refusal_page_and_nothing_is_created(
        client, ctx, monkeypatch, body, sql, status):
    keepable(ctx)
    _while_the_body_is_read(monkeypatch, lambda: _execute(ctx, sql))

    response = post_retry(client, body)

    assert response.status_code == status
    assert response.headers["content-type"].startswith("text/html")
    for leaked in (TASK_ID, "not json", "Traceback"):
        assert leaked not in response.text
    assert [task.id for task in ctx.store.list().tasks] == []
