from factory.task import TaskState
from webhelpers import add

pytest_plugins = ["webfixtures"]

TASK = "20260102-030405"
OTHER = "20260102-030406"


def test_the_list_shows_a_state_humanised_while_the_task_page_still_links_by_raw_state(client, ctx):
    add(ctx, TASK, TaskState.OPEN_QUESTIONS)
    add(ctx, OTHER, TaskState.REVIEWED)

    assert "Open questions" in client.get("/ui/").text
    assert "open_questions" not in client.get("/ui/").text
    answer = client.get(f"/ui/tasks/{TASK}").text
    assert "Open questions" in answer and f'href="/ui/tasks/{TASK}/answer"' in answer
    reviewed = client.get(f"/ui/tasks/{OTHER}").text
    assert f'href="/ui/tasks/{OTHER}/approve"' in reviewed and f'href="/ui/tasks/{OTHER}/reject"' in reviewed
