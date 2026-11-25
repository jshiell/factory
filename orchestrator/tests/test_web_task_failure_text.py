from factory.task import TaskState
from factory.web.routes import _task_fragment
from test_web_task_job import PAGE, TASK
from webhelpers import add

pytest_plugins = ["webfixtures"]

DESCRIPTION = "The tests failed after the agent's change (and any fix rounds). Read the test output; retry or fix by hand."
ESCAPED_DESCRIPTION = DESCRIPTION.replace("'", "&#39;")


def test_a_known_failure_reason_shows_its_description_beside_the_raw_code(client, ctx):
    add(ctx, TASK, TaskState.FAILED, failure_reason="tests_failed")

    for page in (client.get(PAGE).text, _task_fragment(ctx.store, client.app.state.jobs, TASK)):
        assert f"<p>{ESCAPED_DESCRIPTION}</p>" in page
        assert "<p>failure: tests_failed</p>" in page
        assert page.index(ESCAPED_DESCRIPTION) < page.index("failure: tests_failed")


def test_a_composed_fix_reason_shows_its_description(client, ctx):
    add(ctx, TASK, TaskState.FAILED, failure_reason="fix_tests_failed")

    assert "While fixing the failing tests: " in client.get(PAGE).text
