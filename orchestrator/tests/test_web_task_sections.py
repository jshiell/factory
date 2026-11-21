import pytest

from webhelpers import add

pytest_plugins = ["webfixtures"]

ABSENT_FOR_A_BARE_TASK = ["failure", "risks", "review findings", "audit findings", "waived findings", "<pre>", "open questions",
                          "retry of", "fix rounds", "attempts", "denials", "worktree", "branch", "plan error", "review error",
                          "audit error"]


@pytest.mark.parametrize("label, field", [("risks", "plan_risks"), ("review findings", "review_findings"),
                                          ("audit findings", "audit_findings"), ("waived findings", "waived_findings")])
def test_a_list_section_appears_with_its_items(client, ctx, label, field):
    add(ctx, **{field: ("first item", "second item")})

    page = client.get("/ui/tasks/20260102-030405").text

    assert label in page
    assert "<li>first item</li>" in page and "<li>second item</li>" in page


def test_the_plan_appears_in_a_pre_block(client, ctx):
    add(ctx, plan="step one\n  step two")

    page = client.get("/ui/tasks/20260102-030405").text

    assert "<pre>step one\n  step two</pre>" in page


@pytest.mark.parametrize("label", ABSENT_FOR_A_BARE_TASK)
def test_guard_an_optional_section_is_absent_for_a_bare_task(client, ctx, label):
    add(ctx)

    assert label not in client.get("/ui/tasks/20260102-030405").text
