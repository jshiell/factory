from factory.audit_task import AUDIT_CONFIG, build_audit_prompt
from factory.task import Task


def test_audit_prompt_contains_the_task_prompt_and_the_diff_marked_untrusted():
    prompt = build_audit_prompt(Task(id="t1", prompt="add subtract"), "+def subtract(a, b): ...")

    assert "add subtract" in prompt
    assert "+def subtract(a, b): ..." in prompt
    assert "untrusted" in prompt.lower()
    assert not prompt.startswith("-")


def test_audit_config_is_read_only_with_a_verdict_schema_and_a_modest_budget():
    assert AUDIT_CONFIG.allowed_tools == ("Read", "Grep", "Glob")
    assert set(AUDIT_CONFIG.disallowed_tools) == {"Write", "Edit", "NotebookEdit", "Bash"}
    assert AUDIT_CONFIG.permission_mode is None
    assert AUDIT_CONFIG.json_schema["properties"]["verdict"]["enum"] == ["pass", "fail"]
    assert AUDIT_CONFIG.max_turns is not None
    assert 0 < AUDIT_CONFIG.budget_usd < 0.5
