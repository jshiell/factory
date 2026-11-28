import re

from factory.digest import plan_digest
from factory.review_notes import MAX_ITEMS
from factory.task import TaskState
from webhelpers import add

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"


def _forms(text):
    return re.findall(r"<form\b[^>]*>.*?</form>", text, re.S)


def keepable(ctx, findings, **fields):
    return add(ctx, TASK_ID, TaskState.FAILED, **{
        "prompt": "approved prompt", "goal": "fix the widget", "plan": "the plan", "plan_digest": plan_digest("the plan"),
        "review_findings": findings, "failure_reason": "audit_rejected", "audit_findings": ("gap one",), **fields})


def test_the_keep_plan_form_shows_the_reviewer_notes_and_a_waive_checkbox_for_each_required_one(client, ctx):
    keepable(ctx, ("Recommend (required): fix <x>", "Recommend (optional): rename it"))

    text = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/retry").text

    plain, keep = _forms(text)
    assert 'name="waive"' not in plain
    boxes = re.findall(r'<input\b[^>]*name="waive"[^>]*>', keep)
    assert len(boxes) == 1 and 'type="checkbox"' in boxes[0] and 'value="Recommend (required): fix &lt;x&gt;"' in boxes[0]
    assert "[1] Recommend (required): fix &lt;x&gt;" in text and "Recommend (optional): rename it" in text
    assert "not sent to the agents" in text
    assert "An audit finding that quotes a waived note is not sent to the new task." in text


def test_the_keep_plan_page_warns_when_required_notes_were_dropped_by_the_cap(client, ctx):
    keepable(ctx, tuple(f"Recommend (required): note {i}" for i in range(MAX_ITEMS + 2)))

    text = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/retry").text

    assert "WARNING: 2 required note(s)" in text
    assert len(re.findall(r'name="waive"', text)) == MAX_ITEMS
