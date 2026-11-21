from factory.digest import plan_digest
from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
PLAN = "1. add the thing"


def test_posting_a_digest_that_is_not_the_plans_is_a_409_plan_changed_page_and_changes_nothing(client, ctx):
    reviewed = add(ctx, TASK_ID, TaskState.REVIEWED, goal="fix the widget", plan=PLAN, plan_digest=plan_digest(PLAN))
    stale = plan_digest("1. an older plan")

    response = client.post(f"/ui/tasks/{TASK_ID}/approve", data={"plan_digest": stale},
                           headers=same_origin_headers(), follow_redirects=False)

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("text/html")
    assert_ui_security_headers(response)
    assert "plan changed" in response.text
    assert "<form" not in response.text
    for echoed in (TASK_ID, "fix the widget", stale, plan_digest(PLAN), "Traceback"):
        assert echoed not in response.text
    assert ctx.store.load(TASK_ID) == reviewed
