import pytest

from factory.web.routes import _task_fragment
from webhelpers import add

pytest_plugins = ["webfixtures"]

TASK = "20260102-030405"
PAGE = f"/ui/tasks/{TASK}"


def job_in_state(client, state, kind="advance", result=None, error=None):
    jobs = client.app.state.jobs
    job = jobs.create(kind, TASK)
    if state == "running":
        jobs.mark_running(job.id)
    elif state == "succeeded":
        jobs.mark_succeeded(job.id, result or {})
    elif state == "failed":
        jobs.mark_failed(job.id, error or "boom")
    elif state == "interrupted":
        jobs.interrupt_unfinished()
    return job


@pytest.mark.parametrize("state", ["queued", "running", "succeeded", "failed", "interrupted"])
def test_the_task_page_shows_the_latest_job_kind_and_state(client, ctx, state):
    add(ctx, TASK)
    job_in_state(client, state, kind="rebase")

    assert f"<p>job: Rebase, {state.capitalize()}</p>" in client.get(PAGE).text


def test_the_task_page_shows_the_newest_job_only(client, ctx):
    add(ctx, TASK)
    job_in_state(client, "failed")
    job_in_state(client, "queued", kind="merge")

    page = client.get(PAGE).text

    assert "job: Merge, Queued" in page and "job: Advance" not in page


def test_the_task_page_shows_no_job_when_the_task_has_none(client, ctx):
    add(ctx, TASK)

    assert "job:" not in client.get(PAGE).text


def test_the_task_page_shows_the_error_of_a_failed_job(client, ctx):
    add(ctx, TASK)
    job_in_state(client, "failed", error="merge refused")

    assert "<p>job error: merge refused</p>" in client.get(PAGE).text


def test_the_task_page_escapes_a_hostile_job_error(client, ctx):
    add(ctx, TASK)
    job_in_state(client, "failed", error="<script>alert(1)</script>\x1b[31m\x00 bell\x07")

    page = client.get(PAGE).text

    assert "<script>alert" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;\\x1b[31m\\x00 bell\\x07" in page


def test_the_task_page_caps_a_long_job_error(client, ctx):
    add(ctx, TASK)
    job_in_state(client, "failed", error="e" * 5000)

    page = client.get(PAGE).text

    assert "e" * 500 + "…" in page and "e" * 501 not in page


def test_the_task_page_shows_where_a_succeeded_advance_stopped(client, ctx):
    add(ctx, TASK)
    job_in_state(client, "succeeded", result={"stopped": "needs <you>\x1b"})

    page = client.get(PAGE).text

    assert "<p>stopped: needs &lt;you&gt;\\x1b</p>" in page


def test_a_succeeded_job_without_stopped_shows_no_stopped_line(client, ctx):
    add(ctx, TASK)
    job_in_state(client, "succeeded", result={"stopped": None})

    assert "stopped:" not in client.get(PAGE).text


def test_a_job_without_an_error_shows_no_error_line(client, ctx):
    add(ctx, TASK)
    job_in_state(client, "succeeded")

    assert "job error" not in client.get(PAGE).text


def test_the_live_fragment_shows_the_same_job_panel_as_the_page(client, ctx):
    add(ctx, TASK)
    job_in_state(client, "failed", error="bad <thing>")

    fragment = _task_fragment(client.app.state.store, client.app.state.jobs, TASK)
    page = client.get(PAGE).text

    assert fragment in page
    assert "<p>job: Advance, Failed</p>" in fragment and "<p>job error: bad &lt;thing&gt;</p>" in fragment
