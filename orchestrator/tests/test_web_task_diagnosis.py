from factory.task import TaskState
from factory.web.routes import _task_fragment
from test_web_task_job import PAGE, TASK
from webhelpers import add

pytest_plugins = ["webfixtures"]

DIAGNOSIS = {"cause": "the test asserts the wrong sign", "evidence": ["line 3 of test_math.py", "assert -1 == 1"],
             "next_step": "fix the expected value"}


def pages(client, ctx):
    fragment = _task_fragment(ctx.store, client.app.state.repos["proj"].jobs, TASK, "/ui/repos/proj")
    return [client.get(PAGE).text, fragment]


def test_a_stored_diagnosis_is_shown_in_the_failure_block_labelled_as_agent_written(client, ctx):
    add(ctx, TASK, TaskState.FAILED, failure_reason="tests_failed", diagnosis=DIAGNOSIS)

    for page in pages(client, ctx):
        block = page[page.index('<div class="failure">'):]
        block = block[:block.index("</div>")]
        assert "Diagnosis (written by an agent)" in block
        assert "<p>Cause: the test asserts the wrong sign</p>" in block
        assert "<li>line 3 of test_math.py</li>" in block and "<li>assert -1 == 1</li>" in block
        assert "<p>Next step: fix the expected value</p>" in block


def test_a_failed_task_without_a_diagnosis_shows_no_diagnosis_section(client, ctx):
    add(ctx, TASK, TaskState.FAILED, failure_reason="tests_failed")

    for page in pages(client, ctx):
        assert "Diagnosis" not in page
