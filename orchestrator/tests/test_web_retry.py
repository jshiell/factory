from urllib.parse import urlencode

import pytest

from factory.digest import plan_digest
from factory.retry_task import RETRYABLE, RetryRefused
from factory.store import TaskNotFound
from factory.task import TaskState
from factory.waiver import InvalidWaiver
from test_web_retry_page import TASK_ID, keepable
from webhelpers import add, assert_ui_security_headers
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

FORM = "application/x-www-form-urlencoded"
NOT_RETRYABLE = [state for state in TaskState if state not in RETRYABLE]


def post_retry(client, body=b"", task_id=TASK_ID, content_type=FORM, headers=None):
    base = same_origin_headers() if headers is None else headers
    return client.post(f"/ui/tasks/{task_id}/retry", content=body, follow_redirects=False,
                       headers={**base, "Content-Type": content_type})


def keep_form(digest: str) -> bytes:
    return urlencode({"keep_plan": "1", "plan_digest": digest}).encode()


def task_ids(ctx) -> list[str]:
    return sorted(task.id for task in ctx.store.list().tasks)


def new_task(ctx):
    (new_id,) = [task_id for task_id in task_ids(ctx) if task_id != TASK_ID]
    return ctx.store.load(new_id)


def test_posting_a_plain_retry_creates_a_proposed_task_and_redirects_to_it(client, ctx):
    add(ctx, TASK_ID, TaskState.FAILED, goal="fix the widget")

    response = post_retry(client)

    assert response.status_code == 303
    new = new_task(ctx)
    assert response.headers["location"] == f"/ui/tasks/{new.id}"
    assert (new.state, new.goal, new.retry_of, new.plan) == (TaskState.PROPOSED, "fix the widget", TASK_ID, None)
    assert client.app.state.jobs.list() == []


@pytest.mark.parametrize("state, carried", [(TaskState.FAILED, ("gap one",)), (TaskState.AUDITED, ())])
def test_posting_a_keep_plan_retry_with_the_shown_digest_queues_a_task_with_the_plan_and_redirects_to_it(
        client, ctx, state, carried):
    keepable(ctx, state=state)

    response = post_retry(client, keep_form(plan_digest("the plan")))

    assert response.status_code == 303
    new = new_task(ctx)
    assert response.headers["location"] == f"/ui/tasks/{new.id}"
    assert (new.state, new.plan, new.retry_of) == (TaskState.QUEUED, "the plan", TASK_ID)
    assert new.prior_audit_findings == carried and new.waived_findings == ()
    assert client.app.state.jobs.list() == []


def _assert_refused_and_nothing_created(response, ctx, before, status, *shown):
    assert response.status_code == status
    assert response.headers["content-type"].startswith("text/html")
    assert_ui_security_headers(response)
    assert "<form" not in response.text and '<a href="/ui/">' in response.text
    for text in shown:
        assert text in response.text
    for leaked in (TASK_ID, "Traceback"):
        assert leaked not in response.text
    assert task_ids(ctx) == [TASK_ID]
    assert ctx.store.load(TASK_ID) == before


STALE_DIGESTS = {
    "another plan's": lambda digest: plan_digest("another plan"),
    "upper case": lambda digest: digest.upper(),
    "short prefix": lambda digest: digest[:8],
    "whitespace": lambda digest: " \t",
    "trailing space": lambda digest: digest + " ",
    "trailing nul": lambda digest: digest + "\x00",
    "markup": lambda digest: "<script>alert(1)</script>",
    "non-ASCII": lambda digest: "é" + digest,
    "long": lambda digest: "a" * 4000,
}


@pytest.mark.parametrize("make_digest", STALE_DIGESTS.values(), ids=STALE_DIGESTS.keys())
def test_posting_a_keep_plan_retry_with_any_digest_but_the_plans_is_a_409_stale_page_that_creates_nothing(
        client, ctx, make_digest):
    before = keepable(ctx)
    shown = make_digest(plan_digest("the plan"))

    response = post_retry(client, keep_form(shown))

    _assert_refused_and_nothing_created(response, ctx, before, 409, "changed since you opened this page")
    for echoed in (shown, "<script>", "the plan"):
        assert echoed not in response.text


@pytest.mark.parametrize("fields", [
    {"plan": None, "plan_digest": None},
    {"prompt": ""},
    {"plan_digest": plan_digest("an older plan")},
], ids=["no plan", "plan never approved", "stored digest does not match the plan"])
def test_posting_a_keep_plan_retry_for_a_plan_that_cannot_be_kept_is_a_409_that_creates_nothing(client, ctx, fields):
    before = keepable(ctx, **fields)

    response = post_retry(client, keep_form(plan_digest("the plan")))

    _assert_refused_and_nothing_created(response, ctx, before, 409, "changed since you opened this page")


def _retry_raising(client, monkeypatch, error):
    def refuse(task_id, plan_digest=None, waive=(), reinstate=()):
        raise error
    monkeypatch.setattr(client.app.state.actions, "retry", refuse)


@pytest.mark.parametrize("body, error, status, shown", [
    (b"", RetryRefused("<script>x</script> is merged"), 409, "cannot be retried in its current state"),
    (keep_form(plan_digest("the plan")), RetryRefused("<script>x</script> mismatch"), 409, "changed since you opened"),
    (keep_form(plan_digest("the plan")), InvalidWaiver("<script>x</script> not a note"), 409, "cannot be retried"),
    (b"", TaskNotFound(TASK_ID), 404, ""),
    (b"", ValueError("<script>x</script> not json"), 500, ""),
], ids=["plain refused", "keep plan refused", "invalid waiver", "gone", "unreadable"])
def test_a_refusal_from_the_retry_itself_is_an_escaped_html_page_that_creates_nothing(
        client, ctx, monkeypatch, body, error, status, shown):
    before = keepable(ctx)
    _retry_raising(client, monkeypatch, error)

    response = post_retry(client, body)

    _assert_refused_and_nothing_created(response, ctx, before, status, shown)
    for leaked in ("<script>", "not json", "not a note", "mismatch"):
        assert leaked not in response.text


@pytest.mark.parametrize("state", NOT_RETRYABLE)
@pytest.mark.parametrize("body, content_type", [(b"", FORM), (keep_form(plan_digest("the plan")), FORM),
                                                (b"not a form", "text/plain")], ids=["plain", "keep plan", "bad form"])
def test_posting_a_retry_for_a_task_that_is_not_failed_or_audited_is_a_409_that_creates_nothing(
        client, ctx, state, body, content_type):
    before = keepable(ctx, state=state)

    response = post_retry(client, body, content_type=content_type)

    _assert_refused_and_nothing_created(response, ctx, before, 409, "cannot be retried")


def test_posting_a_retry_for_a_failed_task_without_a_goal_is_a_409_that_creates_nothing(client, ctx):
    before = add(ctx, TASK_ID, TaskState.FAILED, prompt="fix the widget")

    _assert_refused_and_nothing_created(post_retry(client), ctx, before, 409, "cannot be retried")
