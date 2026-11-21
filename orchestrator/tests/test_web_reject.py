from factory.task import TaskState
from webhelpers import add
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]


def test_posting_a_reject_fails_the_task_and_redirects_to_its_page(client, ctx):
    add(ctx, "20260102-030405", TaskState.REVIEWED, goal="fix the widget")

    response = client.post("/ui/tasks/20260102-030405/reject", headers=same_origin_headers(), follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/tasks/20260102-030405"
    task = ctx.store.load("20260102-030405")
    assert task.state is TaskState.FAILED
    assert task.failure_reason == "plan_rejected_by_human"
