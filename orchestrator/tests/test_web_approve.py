from factory.approve_plan import build_implementation_prompt
from factory.digest import plan_digest
from factory.review_notes import MAX_ITEMS
from factory.task import TaskState
from webhelpers import add
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
PLAN = "1. add the thing\n2. test it"
FINDINGS = tuple(f"Recommend (required): note {i}" for i in range(MAX_ITEMS + 2)) + ("Recommend (optional): rename it",)


def _reviewed(ctx, **fields):
    return add(ctx, TASK_ID, TaskState.REVIEWED, goal="fix the widget", plan=PLAN, plan_digest=plan_digest(PLAN),
               review_findings=FINDINGS, **fields)


def _post_approve(client, fields):
    return client.post(f"/ui/tasks/{TASK_ID}/approve", data=fields, headers=same_origin_headers(), follow_redirects=False)


def test_posting_the_shown_digest_queues_the_task_without_waivers_and_redirects_to_the_task_page(client, ctx):
    _reviewed(ctx)

    response = _post_approve(client, {"plan_digest": plan_digest(PLAN)})

    assert response.status_code == 303
    assert response.headers["location"] == f"/ui/tasks/{TASK_ID}"
    task = ctx.store.load(TASK_ID)
    assert task.state is TaskState.QUEUED
    assert task.waived_findings == ()
    assert task.prompt == build_implementation_prompt("fix the widget", PLAN, FINDINGS)
    assert f"note {MAX_ITEMS - 1}\n" in task.prompt and f"note {MAX_ITEMS}\n" not in task.prompt
