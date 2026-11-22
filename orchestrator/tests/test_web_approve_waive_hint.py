from factory.digest import plan_digest
from factory.task import TaskState
from webhelpers import add

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
PLAN = "1. add the thing"


def test_the_approve_page_tells_the_reader_the_terminal_command_that_waives_a_note(client, ctx):
    add(ctx, TASK_ID, TaskState.REVIEWED, goal="fix the widget", plan=PLAN, plan_digest=plan_digest(PLAN),
        review_findings=("Recommend (required): cover edge cases",))

    page = client.get(f"/ui/tasks/{TASK_ID}/approve").text

    assert f"To waive a note, run <code>factory approve {TASK_ID}</code> in a terminal instead." in page
    assert "needs the CLI" not in page
