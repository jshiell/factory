import re

import pytest

from factory.digest import plan_digest
from factory.retry_task import RETRYABLE
from factory.store import TaskNotFound
from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
NOT_RETRYABLE = [state for state in TaskState if state not in RETRYABLE]
HOSTILE = "<script>alert(1)</script>\x1b[31m & \"quoted\""
ESCAPED = "&lt;script&gt;alert(1)&lt;/script&gt;\\x1b[31m &amp; &#34;quoted&#34;"


def _forms(text):
    return re.findall(r"<form\b[^>]*>.*?</form>", text, re.S)


def _controls(form):
    return re.findall(r"<(?:input|button|select|textarea)\b[^>]*>", form)


def keepable(ctx, state=TaskState.FAILED, **fields):
    """A task the audit rejected whose plan was approved: retry can keep it, carrying the audit's findings."""
    return add(ctx, TASK_ID, state, **{
        "prompt": "approved prompt", "goal": "fix the widget", "plan": "the plan", "plan_digest": plan_digest("the plan"),
        "review_findings": ("RECOMMEND (required): fix x",), "failure_reason": "audit_rejected",
        "audit_findings": ("gap one",), **fields})


def test_the_retry_page_of_a_task_without_a_plan_offers_only_a_plain_retry_posting_to_the_same_path(client, ctx):
    add(ctx, TASK_ID, TaskState.FAILED, goal="fix <b>the</b> widget")

    response = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/retry")

    assert response.status_code == 200
    assert_ui_security_headers(response)
    assert "fix &lt;b&gt;the&lt;/b&gt; widget" in response.text and "<b>" not in response.text
    assert "new task" in response.text
    forms = _forms(response.text)
    assert len(forms) == 1
    assert f'action="/ui/repos/proj/tasks/{TASK_ID}/retry"' in forms[0]
    assert [control.startswith("<button") for control in _controls(forms[0])] == [True]
    assert "Retry from scratch" in forms[0]
    assert "plan_digest" not in response.text


def test_the_retry_page_of_a_task_with_a_keepable_plan_offers_both_retries_and_shows_what_is_kept(client, ctx):
    keepable(ctx)

    response = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/retry")

    assert response.status_code == 200
    text = response.text
    assert "the plan" in text and "gap one" in text
    assert "spends money" in text
    plain, keep = _forms(text)
    assert "Retry from scratch" in plain and "plan_digest" not in plain
    assert "Retry keeping the plan" in keep
    assert f'action="/ui/repos/proj/tasks/{TASK_ID}/retry"' in keep
    assert _controls(keep) == ['<input type="checkbox" name="waive" value="RECOMMEND (required): fix x">',
                               '<input type="hidden" name="keep_plan" value="1">',
                               f'<input type="hidden" name="plan_digest" value="{plan_digest("the plan")}">',
                               '<button type="submit">']


@pytest.mark.parametrize("state, fields", [
    (TaskState.FAILED, {"failure_reason": "gate_failed"}),
    (TaskState.AUDITED, {}),
], ids=["failed for another reason", "audited"])
def test_the_keep_plan_retry_shows_only_the_audit_findings_the_new_task_carries(client, ctx, state, fields):
    keepable(ctx, state=state, **fields)

    text = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/retry").text

    assert "Retry keeping the plan" in text
    assert "gap one" not in text


@pytest.mark.parametrize("fields", [
    {"plan": None, "plan_digest": None},
    {"prompt": ""},
    {"plan_digest": plan_digest("an older plan")},
], ids=["no plan", "plan never approved", "stored digest does not match the plan"])
def test_the_retry_page_of_a_task_whose_plan_cannot_be_kept_offers_only_a_plain_retry(client, ctx, fields):
    keepable(ctx, **fields)

    text = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/retry").text

    assert len(_forms(text)) == 1
    assert "plan_digest" not in text and "keeping the plan" not in text


def test_the_retry_page_is_shown_while_the_task_has_an_unfinished_job(client, ctx):
    """Retry accepts duplicates (D8): a queued or running job does not refuse it."""
    keepable(ctx)
    job = client.app.state.repos["proj"].jobs.create("advance", TASK_ID)
    client.app.state.repos["proj"].jobs.mark_running(job.id)

    response = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/retry")

    assert response.status_code == 200
    assert len(_forms(response.text)) == 2


def test_the_keep_plan_retry_page_shows_where_each_plan_link_really_goes(client, ctx):
    plan = "see [the docs](https://evil.test/x)"
    keepable(ctx, plan=plan, plan_digest=plan_digest(plan))

    text = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/retry").text

    assert "the docs</a> (https://evil.test/x)" in text


def test_everything_on_the_keep_plan_retry_page_is_escaped(client, ctx):
    keepable(ctx, goal=HOSTILE, plan=HOSTILE, plan_digest=plan_digest(HOSTILE), audit_findings=(HOSTILE, "<b>bold</b>"))

    text = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/retry").text

    assert text.count(ESCAPED) == 2
    assert ESCAPED.replace("&#34;", "&quot;") in text  # the markdown-rendered plan
    assert "&lt;b&gt;bold&lt;/b&gt;" in text
    for raw in ("<script>", "\x1b", "<b>"):
        assert raw not in text


@pytest.mark.parametrize("error, status", [(TaskNotFound(TASK_ID), 404), (ValueError("not json"), 500)],
                         ids=["gone", "unreadable"])
def test_a_task_that_goes_or_breaks_before_the_keepable_check_loads_it_gets_the_task_refusal_page(
        client, ctx, monkeypatch, error, status):
    keepable(ctx)

    def fail(task_id):
        raise error
    monkeypatch.setattr(client.app.state.repos["proj"].actions, "keepable_plan", fail)

    response = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/retry")

    assert response.status_code == status
    assert response.headers["content-type"].startswith("text/html")
    assert "<form" not in response.text
    for leaked in (TASK_ID, "not json", "the plan", "Traceback"):
        assert leaked not in response.text


def _assert_wrong_state_page(response):
    assert response.status_code == 409
    assert_ui_security_headers(response)
    assert "cannot be retried" in response.text
    assert "<form" not in response.text and '<a href="/ui/">' in response.text
    for echoed in (TASK_ID, "fix the widget"):
        assert echoed not in response.text


@pytest.mark.parametrize("state", NOT_RETRYABLE)
def test_the_retry_page_of_a_task_that_is_not_failed_or_audited_is_a_409_without_form_or_id(client, ctx, state):
    task = add(ctx, TASK_ID, state, goal="fix the widget")

    _assert_wrong_state_page(client.get(f"/ui/repos/proj/tasks/{TASK_ID}/retry"))
    assert ctx.store.load(TASK_ID) == task


def test_the_retry_page_of_a_failed_task_without_a_goal_is_a_409(client, ctx):
    add(ctx, TASK_ID, TaskState.FAILED, prompt="fix the widget")

    _assert_wrong_state_page(client.get(f"/ui/repos/proj/tasks/{TASK_ID}/retry"))
