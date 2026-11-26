import pytest

from factory.task import TaskState
from factory.web.routes import _task_fragment
from test_web_task_job import PAGE, TASK, job_in_state
from webhelpers import add

pytest_plugins = ["webfixtures"]

DESCRIPTION = "The tests failed after the agent's change (and any fix rounds). Read the test output; retry or fix by hand."
ESCAPED_DESCRIPTION = DESCRIPTION.replace("'", "&#39;")


def test_a_known_failure_reason_shows_its_description_beside_the_raw_code(client, ctx):
    add(ctx, TASK, TaskState.FAILED, failure_reason="tests_failed")

    for page in (client.get(PAGE).text, _task_fragment(ctx.store, client.app.state.repos["proj"].jobs, TASK, "/ui/repos/proj")):
        assert f"<p>{ESCAPED_DESCRIPTION}</p>" in page
        assert "<p>Failure: tests_failed</p>" in page
        assert page.index(ESCAPED_DESCRIPTION) < page.index("Failure: tests_failed")


def test_a_composed_fix_reason_shows_its_description(client, ctx):
    add(ctx, TASK, TaskState.FAILED, failure_reason="fix_tests_failed")

    assert "While fixing the failing tests: " in client.get(PAGE).text


def test_a_no_progress_stop_describes_its_stage_error_beside_the_raw_code(client, ctx):
    add(ctx, TASK)
    job_in_state(client, "succeeded", result={"stopped": "plan: no progress (plan_malformed)",
                                              "stopped_error": {"field": "plan_error", "code": "plan_malformed"}})

    page = client.get(PAGE).text

    assert "<p>Stopped: plan: no progress (plan_malformed)</p>" in page
    assert "<p>Plan error: The plan came back in a form that could not be read. Advance again to re-plan. (plan_malformed)</p>" in page


def test_a_free_text_stop_renders_the_job_section_exactly_as_before_descriptions(client, ctx):
    add(ctx, TASK)
    job_in_state(client, "succeeded", result={"stopped": "step cap (10) reached"})

    fragment = _task_fragment(ctx.store, client.app.state.repos["proj"].jobs, TASK, "/ui/repos/proj")

    assert '<p>Job: Advance, Succeeded</p>\n\n\n<p>Stopped: step cap (10) reached</p>\n\n</section>\n' in fragment


def test_the_tasks_list_describes_a_known_failure_reason_and_leaves_free_text_alone(client, ctx):
    add(ctx, TASK, TaskState.FAILED, failure_reason="timeout")
    add(ctx, "20260102-030406", TaskState.FAILED, failure_reason="gate failed")

    page = client.get("/ui/").text

    assert "<td>The agent ran out of time. Retry the task. (timeout)</td>" in page
    assert "<td>gate failed</td>" in page


@pytest.mark.parametrize("field, label, code, sentence", [
    ("plan_error", "Plan error", "plan_malformed", "The plan came back in a form that could not be read."),
    ("review_error", "Review error", "review_malformed", "The review came back unreadable."),
    ("audit_error", "Audit error", "audit_malformed", "The audit came back unreadable."),
])
def test_a_known_stage_error_shows_its_description_beside_the_raw_code(client, ctx, field, label, code, sentence):
    add(ctx, TASK, **{field: code})

    page = client.get(PAGE).text

    assert f"<p>{label}: {sentence} Advance again" in page
    assert f"({code})</p>" in page
