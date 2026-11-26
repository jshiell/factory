import pytest

from factory.task import TaskState
from factory.textsafe import escape_control
from webhelpers import add

pytest_plugins = ["webfixtures"]

ABSENT_FOR_A_BARE_TASK = ["Failure", "Risks", "Review findings", "Audit findings", "Waived findings", "<pre>", "Open questions",
                          "Retry of", "Fix rounds", "Attempts", "Denials", "Worktree", "Branch", "Plan error", "Review error",
                          "Audit error"]


@pytest.mark.parametrize("label, field", [("Risks", "plan_risks"), ("Review findings", "review_findings"),
                                          ("Audit findings", "audit_findings"), ("Waived findings", "waived_findings")])
def test_a_list_section_appears_with_its_items(client, ctx, label, field):
    add(ctx, **{field: ("first item", "second item")})

    page = client.get("/ui/repos/proj/tasks/20260102-030405").text

    assert label in page
    assert "<li>first item</li>" in page and "<li>second item</li>" in page


def test_the_plan_is_rendered_as_markdown(client, ctx):
    add(ctx, plan="## step one\n\n- **bold** item")

    page = client.get("/ui/repos/proj/tasks/20260102-030405").text

    assert '<div class="plan"><h2>step one</h2>' in page
    assert "<li><strong>bold</strong> item</li>" in page


def test_open_questions_show_the_round_titles_and_recommended_answers(client, ctx):
    questions = ({"number": 1, "title": "which database", "recommended": "sqlite"},
                 {"number": 2, "title": "which port", "recommended": "8765"})
    add(ctx, state=TaskState.OPEN_QUESTIONS, grill_rounds=2, open_questions=questions)

    page = client.get("/ui/repos/proj/tasks/20260102-030405").text

    assert "Open questions (round 2)" in page
    for shown in ("1. which database", "recommended: sqlite", "2. which port", "recommended: 8765"):
        assert shown in page


@pytest.mark.parametrize("fields, shown", [
    ({"retry_of": "20260101-000000"}, ["Retry of: 20260101-000000"]),
    ({"fix_rounds_used": 2}, ["Fix rounds: 2"]),
    ({"attempts": 3}, ["Attempts: 3"]),
    ({"denials": ({"tool": "a"}, {"tool": "b"})}, ["Denials: 2"]),
    ({"worktree": "/work/tree"}, ["Worktree: /work/tree", "Branch: factory/20260102-030405"]),
    ({"plan_error": "p broke"}, ["Plan error: p broke"]),
    ({"review_error": "r broke"}, ["Review error: r broke"]),
    ({"audit_error": "a broke"}, ["Audit error: a broke"]),
])
def test_a_scalar_field_appears_when_set(client, ctx, fields, shown):
    add(ctx, **fields)

    page = client.get("/ui/repos/proj/tasks/20260102-030405").text

    for text in shown:
        assert text in page


MARKUP = "<script>alert(1)</script>"
ESCAPED_MARKUP = "&lt;script&gt;alert(1)&lt;/script&gt;"
FREE_TEXT_FIELDS = {
    "goal": MARKUP, "failure_reason": MARKUP, "plan": MARKUP, "plan_risks": (MARKUP,), "review_findings": (MARKUP,),
    "audit_findings": (MARKUP,), "waived_findings": (MARKUP,), "retry_of": MARKUP, "worktree": MARKUP,
    "plan_error": MARKUP, "review_error": MARKUP, "audit_error": MARKUP,
    "open_questions": ({"number": 1, "title": MARKUP, "recommended": MARKUP},),
}


@pytest.mark.parametrize("field", FREE_TEXT_FIELDS)
def test_guard_markup_in_a_free_text_field_is_escaped(client, ctx, field):
    add(ctx, **{field: FREE_TEXT_FIELDS[field]})

    page = client.get("/ui/repos/proj/tasks/20260102-030405").text

    assert MARKUP not in page
    assert ESCAPED_MARKUP in page


CONTROL = "red\x1b[31m and \u202ebidi\u200b"
CONTROL_FIELDS = {**{field: CONTROL if isinstance(value, str) else (CONTROL,) for field, value in FREE_TEXT_FIELDS.items()},
                  "open_questions": ({"number": 1, "title": CONTROL, "recommended": CONTROL},)}


@pytest.mark.parametrize("field", [*CONTROL_FIELDS, "prompt"])
def test_control_and_bidi_characters_in_a_free_text_field_are_escaped(client, ctx, field):
    fields = {"goal": None, "prompt": CONTROL} if field == "prompt" else {field: CONTROL_FIELDS[field]}
    add(ctx, **fields)

    page = client.get("/ui/repos/proj/tasks/20260102-030405").text

    assert not any(char in page for char in "\x1b\u202e\u200b")
    assert escape_control(CONTROL) in page


@pytest.mark.parametrize("label", ABSENT_FOR_A_BARE_TASK)
def test_guard_an_optional_section_is_absent_for_a_bare_task(client, ctx, label):
    add(ctx)

    assert label not in client.get("/ui/repos/proj/tasks/20260102-030405").text
