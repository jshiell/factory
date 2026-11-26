from factory.task import TaskState
from webhelpers import add

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
HOSTILE = "<script>alert(1)</script>\x1b[31m & \"quoted\""


def test_a_hostile_goal_is_escaped_on_the_abandon_page(client, ctx):
    add(ctx, TASK_ID, TaskState.QUEUED, goal=HOSTILE)

    text = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/abandon").text

    assert "&lt;script&gt;alert(1)&lt;/script&gt;\\x1b[31m &amp; &#34;quoted&#34;" in text
    assert "<script>" not in text and "\x1b" not in text


def test_a_task_without_a_goal_shows_its_escaped_prompt(client, ctx):
    add(ctx, TASK_ID, TaskState.QUEUED, prompt="do <i>this</i>")

    text = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/abandon").text

    assert "do &lt;i&gt;this&lt;/i&gt;" in text and "<i>" not in text
