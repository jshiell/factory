import re
from urllib.parse import urlencode

from factory.digest import plan_digest
from factory.review_notes import MAX_ITEMS
from factory.task import TaskState
from test_web_retry import new_task, post_retry
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


def keep_form(digest, **fields):
    return urlencode({"keep_plan": "1", "plan_digest": digest, **fields}, doseq=True).encode()


def test_posting_a_waive_value_on_a_keep_plan_retry_queues_the_task_with_that_note_waived_and_its_quoting_gap_dropped(client, ctx):
    note = "Recommend (required): fix x"
    keepable(ctx, (note, "Recommend (required): add y"), audit_findings=(f"you skipped: {note}", "gap two"))

    response = post_retry(client, keep_form(plan_digest("the plan"), waive=note))

    assert response.status_code == 303
    new = new_task(ctx)
    assert new.state is TaskState.QUEUED
    assert new.waived_findings == (note,)
    assert new.review_findings == ("Recommend (required): add y",)
    assert new.prior_audit_findings == ("gap two",)


def test_a_waive_value_that_is_no_required_note_is_refused_with_a_409_naming_no_note_and_queues_nothing(client, ctx):
    keepable(ctx, ("Recommend (required): fix x", "Recommend (optional): rename it"))

    for unknown in ("Recommend (required): <invented>", "Recommend (optional): rename it"):
        response = post_retry(client, keep_form(plan_digest("the plan"), waive=unknown))

        assert response.status_code == 409
        assert "invented" not in response.text and "rename it" not in response.text
        assert "waive" in response.text
        assert [task.id for task in ctx.store.list().tasks] == [TASK_ID]


def test_a_retry_from_scratch_that_posts_waive_or_reinstate_is_a_422_and_queues_nothing(client, ctx):
    keepable(ctx, ("Recommend (required): fix x",))

    for field in ("waive", "reinstate"):
        response = post_retry(client, urlencode({field: "Recommend (required): fix x"}).encode())

        assert response.status_code == 422
        assert [task.id for task in ctx.store.list().tasks] == [TASK_ID]


def test_the_keep_plan_form_lists_the_notes_waived_earlier_with_a_reinstate_checkbox_for_each(client, ctx):
    keepable(ctx, ("Recommend (required): add y",), waived_findings=("Recommend (required): fix <x>",))

    text = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/retry").text

    plain, keep = _forms(text)
    assert 'name="reinstate"' not in plain
    boxes = re.findall(r'<input\b[^>]*name="reinstate"[^>]*>', keep)
    assert len(boxes) == 1 and 'type="checkbox"' in boxes[0] and 'value="Recommend (required): fix &lt;x&gt;"' in boxes[0]
    assert "Waived earlier" in keep and "not waived" in keep


def test_the_keep_plan_form_has_no_reinstate_section_when_nothing_was_waived(client, ctx):
    keepable(ctx, ("Recommend (required): add y",))

    assert 'name="reinstate"' not in client.get(f"/ui/repos/proj/tasks/{TASK_ID}/retry").text


def test_waive_and_reinstate_in_one_post_is_refused_and_queues_nothing(client, ctx):
    keepable(ctx, ("Recommend (required): add y",), waived_findings=("Recommend (required): fix x",))

    response = post_retry(client, keep_form(plan_digest("the plan"), waive="Recommend (required): add y",
                                            reinstate="Recommend (required): fix x"))

    assert response.status_code == 409
    assert [task.id for task in ctx.store.list().tasks] == [TASK_ID]


def test_posting_a_reinstate_value_queues_the_task_with_that_note_no_longer_waived(client, ctx):
    earlier, kept = "Recommend (required): fix x", "Recommend (required): old waiver"
    keepable(ctx, ("Recommend (required): add y",), waived_findings=(earlier, kept))

    response = post_retry(client, keep_form(plan_digest("the plan"), reinstate=earlier))

    assert response.status_code == 303
    new = new_task(ctx)
    assert new.waived_findings == (kept,)
    assert earlier in new.review_findings


def test_a_reinstate_value_that_was_never_waived_is_refused_and_queues_nothing(client, ctx):
    keepable(ctx, ("Recommend (required): add y",), waived_findings=("Recommend (required): fix x",))

    for unknown in ("Recommend (required): <invented>", "Recommend (required): add y"):
        response = post_retry(client, keep_form(plan_digest("the plan"), reinstate=unknown))

        assert response.status_code == 409
        assert "invented" not in response.text
        assert [task.id for task in ctx.store.list().tasks] == [TASK_ID]


def test_reinstating_past_the_note_cap_is_refused_and_queues_nothing(client, ctx):
    waived = tuple(f"Recommend (required): old {i}" for i in range(3))
    keepable(ctx, tuple(f"Recommend (required): now {i}" for i in range(MAX_ITEMS)), waived_findings=waived)

    response = post_retry(client, keep_form(plan_digest("the plan"), reinstate=list(waived[:1])))

    assert response.status_code == 409
    assert [task.id for task in ctx.store.list().tasks] == [TASK_ID]


def test_every_waive_or_reinstate_refusal_uses_the_same_neutral_sentence(client, ctx):
    keepable(ctx, ("Recommend (required): add y",), waived_findings=("Recommend (required): fix x",))
    posts = ({"waive": "Recommend (required): nope"}, {"reinstate": "Recommend (required): nope"},
             {"waive": "Recommend (required): add y", "reinstate": "Recommend (required): fix x"})

    for fields in posts:
        text = post_retry(client, keep_form(plan_digest("the plan"), **fields)).text

        assert "cannot be retried with this form" in text
        assert "The waive or reinstate choices in this form cannot be applied to this plan." in text
        assert "waive choice is not" not in text


def test_the_keep_plan_page_warns_when_required_notes_were_dropped_by_the_cap(client, ctx):
    keepable(ctx, tuple(f"Recommend (required): note {i}" for i in range(MAX_ITEMS + 2)))

    text = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/retry").text

    assert "WARNING: 2 required note(s)" in text
    assert len(re.findall(r'name="waive"', text)) == MAX_ITEMS
