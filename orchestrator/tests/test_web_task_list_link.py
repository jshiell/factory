from factory.task import TaskState
from webhelpers import add

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
LIST_LINK = '<a href="/ui/">Back to the task list</a>'


def test_the_task_page_links_back_to_the_list_outside_the_part_the_stream_replaces(client, ctx):
    add(ctx, TASK_ID, TaskState.QUEUED, goal="fix the widget")

    page = client.get(f"/ui/tasks/{TASK_ID}").text

    assert page.count(LIST_LINK) == 1
    assert page.index(LIST_LINK) > page.index('id="task"')
    assert LIST_LINK not in page.split('id="task"', 1)[1].split("</div>", 1)[0]
