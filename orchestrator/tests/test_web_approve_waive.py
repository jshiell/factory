import re

from factory.digest import plan_digest
from factory.task import TaskState
from webhelpers import add

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
PLAN = "1. add the thing"
AWKWARD = 'Recommend (required): odd "quote" <b> \ud800 \x01 ‮ end'


def _reviewed(ctx, findings):
    return add(ctx, TASK_ID, TaskState.REVIEWED, goal="fix the widget", plan=PLAN, plan_digest=plan_digest(PLAN),
               review_findings=findings)


def test_the_approve_page_offers_a_waive_checkbox_for_each_required_note_with_the_escaped_text_as_its_value(client, ctx):
    _reviewed(ctx, ("Recommend (required): cover edge cases", "Recommend (optional): rename it", AWKWARD))

    response = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/approve")

    assert response.status_code == 200
    form = re.findall(r"<form\b[^>]*>.*?</form>", response.text, re.S)[0]
    boxes = re.findall(r'<input\b[^>]*name="waive"[^>]*>', form)
    assert len(boxes) == 2
    assert all('type="checkbox"' in box for box in boxes)
    assert 'value="Recommend (required): cover edge cases"' in boxes[0]
    assert 'value="Recommend (required): odd &#34;quote&#34; &lt;b&gt; \\ud800 \\x01 \\u202e end"' in boxes[1]
    assert "[1] Recommend (required): cover edge cases" in form
    assert "factory approve" not in response.text
    assert "not sent to the agents" in response.text
