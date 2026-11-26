from factory.task import TaskState
from webhelpers import add

pytest_plugins = ["webfixtures"]

TASK = "20260102-030405"


def test_the_jobs_page_lists_jobs_newest_first_with_kind_state_and_task_link(client, ctx):
    add(ctx, TASK, TaskState.QUEUED)
    jobs = client.app.state.repos["proj"].jobs
    jobs.mark_succeeded(jobs.create("advance", TASK).id, {})
    jobs.mark_failed(jobs.create("clean", None).id, "boom")

    page = client.get("/ui/jobs").text

    assert page.index("Clean, Failed") < page.index("Advance, Succeeded")
    assert f'<a href="/ui/repos/proj/tasks/{TASK}">{TASK}</a>' in page
    assert "Error: boom" in page


def test_the_jobs_page_shows_only_the_latest_fifty(client):
    jobs = client.app.state.repos["proj"].jobs
    for _ in range(55):
        jobs.create("clean", None)

    assert client.get("/ui/jobs").text.count("Clean, Queued") == 50


def test_a_job_error_is_capped_and_escaped(client):
    jobs = client.app.state.repos["proj"].jobs
    jobs.mark_failed(jobs.create("clean", None).id, "<script>alert(1)</script>" + "x" * 5000)

    page = client.get("/ui/jobs").text

    assert "<script>alert(1)" not in page
    assert "x" * 600 not in page


def test_the_jobs_page_is_linked_from_the_task_list(client):
    assert '<a href="/ui/jobs">' in client.get("/ui/").text


def test_the_jobs_page_without_a_session_is_401(client):
    client.cookies.clear()

    assert client.get("/ui/jobs").status_code == 401
