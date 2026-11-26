import pytest

from factory.digest import plan_digest
from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
PLAN = "1. add the thing"


def _post_approve(client, fields):
    return client.post(f"/ui/repos/proj/tasks/{TASK_ID}/approve", data=fields, headers=same_origin_headers(), follow_redirects=False)


def _assert_wrong_state_page(response):
    assert response.status_code == 409
    assert_ui_security_headers(response)
    assert "cannot be approved" in response.text
    assert "<form" not in response.text
    for echoed in (TASK_ID, "fix the widget", plan_digest(PLAN)):
        assert echoed not in response.text


@pytest.mark.parametrize("fields", [{"plan_digest": plan_digest(PLAN)}, {}, {"plan_digest": ""}], ids=["valid", "no-digest", "empty-digest"])
@pytest.mark.parametrize("state", [state for state in TaskState if state is not TaskState.REVIEWED])
def test_posting_an_approval_for_a_task_that_is_not_reviewed_is_a_409_and_saves_nothing(client, ctx, state, fields):
    task = add(ctx, TASK_ID, state, goal="fix the widget", plan=PLAN, plan_digest=plan_digest(PLAN))

    _assert_wrong_state_page(_post_approve(client, fields))

    assert ctx.store.load(TASK_ID) == task


def test_a_second_post_after_approval_is_a_409_and_changes_nothing_more(client, ctx):
    add(ctx, TASK_ID, TaskState.REVIEWED, goal="fix the widget", plan=PLAN, plan_digest=plan_digest(PLAN))
    assert _post_approve(client, {"plan_digest": plan_digest(PLAN)}).status_code == 303
    approved = ctx.store.load(TASK_ID)

    _assert_wrong_state_page(_post_approve(client, {"plan_digest": plan_digest(PLAN)}))

    assert ctx.store.load(TASK_ID) == approved
