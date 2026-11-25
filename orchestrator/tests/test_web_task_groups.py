import re

from factory.task import TaskState
from factory.web.routes import _task_fragment
from test_web_task_job import PAGE, TASK, job_in_state
from webhelpers import add

pytest_plugins = ["webfixtures"]

JOB_GROUP = re.compile(r'<section class="job">\s*<p>job: Merge, Failed</p>\s*<p>job error: refused</p>\s*</section>')
FAILURE_GROUP = re.compile(r'<div class="failure">\s*<p>failure: gate failed</p>\s*</div>')


def test_the_latest_job_is_grouped_in_a_section_with_its_error(client, ctx):
    add(ctx, TASK)
    job_in_state(client, "failed", kind="merge", error="refused")

    assert JOB_GROUP.search(client.get(PAGE).text)
    assert JOB_GROUP.search(_task_fragment(ctx.store, client.app.state.jobs, TASK))


def test_there_is_no_job_section_without_a_job(client, ctx):
    add(ctx, TASK)

    assert "job" not in client.get(PAGE).text


def test_the_failure_reason_is_grouped_in_a_callout(client, ctx):
    add(ctx, TASK, TaskState.FAILED, failure_reason="gate failed")

    assert FAILURE_GROUP.search(client.get(PAGE).text)
    assert FAILURE_GROUP.search(_task_fragment(ctx.store, client.app.state.jobs, TASK))
