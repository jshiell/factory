import pytest

from webhelpers import add

pytest_plugins = ["webfixtures"]

ABSENT_FOR_A_BARE_TASK = ["failure", "risks", "review findings", "audit findings", "waived findings", "<pre>", "open questions",
                          "retry of", "fix rounds", "attempts", "denials", "worktree", "branch", "plan error", "review error",
                          "audit error"]


@pytest.mark.parametrize("label", ABSENT_FOR_A_BARE_TASK)
def test_guard_an_optional_section_is_absent_for_a_bare_task(client, ctx, label):
    add(ctx)

    assert label not in client.get("/ui/tasks/20260102-030405").text
