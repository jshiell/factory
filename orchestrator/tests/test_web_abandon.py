from factory.task import TaskState
from webhelpers import add
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
FORM = "application/x-www-form-urlencoded"


def _post_abandon(client, task_id=TASK_ID):
    return client.post(f"/ui/repos/proj/tasks/{task_id}/abandon", content=b"", follow_redirects=False,
                       headers={**same_origin_headers(), "Content-Type": FORM})


def test_posting_an_abandon_queues_the_job_and_redirects_to_the_task_page(client, ctx):
    add(ctx, TASK_ID, TaskState.QUEUED, goal="fix the widget")

    response = _post_abandon(client)

    assert response.status_code == 303
    assert response.headers["location"] == f"/ui/repos/proj/tasks/{TASK_ID}"
    client.app.state.runner.wait_idle()
    job = client.app.state.repos["proj"].jobs.latest_for_task(TASK_ID)
    assert (job.kind, job.state) == ("abandon", "succeeded")
    task = ctx.store.load(TASK_ID)
    assert task.state is TaskState.FAILED and task.failure_reason == "abandoned"
