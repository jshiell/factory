import re
from dataclasses import replace
from pathlib import Path

import pytest

from factory.approve_plan import approve_plan, build_implementation_prompt, reject_plan
from factory.digest import plan_digest
from factory.store import TaskStore
from factory.task import InvalidTransition, Task, TaskState

PLAN = "1. add multiply\n2. add tests"


@pytest.fixture
def store(tmp_path):
    (tmp_path / "tasks").mkdir()
    store = TaskStore(tmp_path / "tasks")
    store.save(Task(id="t1", prompt="", state=TaskState.REVIEWED, goal="add multiply", plan=PLAN,
                    plan_digest=plan_digest(PLAN)))
    return store


def test_approving_with_the_shown_digest_queues_the_task_with_an_implementation_prompt(store):
    task = approve_plan("t1", store, plan_digest(PLAN))

    assert task.state is TaskState.QUEUED
    assert task.prompt == build_implementation_prompt("add multiply", PLAN)
    assert store.load("t1") == task


def test_the_implementation_prompt_has_the_plan_and_the_tamper_rules_and_no_leading_dash():
    prompt = build_implementation_prompt("add multiply", PLAN)

    assert PLAN in prompt and "add multiply" in prompt
    assert "test" in prompt.lower() and "never edit or remove" in prompt.lower()
    assert not prompt.startswith("-")


def test_a_wrong_digest_raises_and_leaves_the_task_unchanged(store):
    before = store.load("t1")

    with pytest.raises(ValueError):
        approve_plan("t1", store, plan_digest("some other plan"))

    assert store.load("t1") == before


def test_a_plan_altered_after_review_with_a_stale_stored_digest_cannot_be_approved(store):
    store.save(replace(store.load("t1"), plan="1. delete all the tests"))
    before = store.load("t1")

    for shown in (plan_digest(PLAN), plan_digest("1. delete all the tests")):
        with pytest.raises(ValueError):
            approve_plan("t1", store, shown)

    assert store.load("t1") == before


@pytest.mark.parametrize("state", [s for s in TaskState if s is not TaskState.REVIEWED])
def test_only_a_reviewed_task_can_be_approved_or_rejected(store, state):
    store.save(replace(store.load("t1"), state=state))

    with pytest.raises(InvalidTransition):
        approve_plan("t1", store, plan_digest(PLAN))
    with pytest.raises(InvalidTransition):
        reject_plan("t1", store)


def test_rejecting_fails_the_task_as_plan_rejected_by_human(store):
    task = reject_plan("t1", store)

    assert (task.state, task.failure_reason) == (TaskState.FAILED, "plan_rejected_by_human")
    assert store.load("t1") == task


def test_tripwire_approval_is_only_wired_in_approve_plan_and_exactly_once_in_the_cli():
    """A tripwire, not a guarantee: 'human-only' is enforced by process (see plans 4 and 5)."""
    source = Path(__file__).parent.parent / "src" / "factory"
    calls = {
        str(path.relative_to(source)): len(re.findall(r"approve_plan\(", path.read_text()))
        for path in source.rglob("*.py")
        if path.name != "approve_plan.py"
    }
    event_users = [
        str(path.relative_to(source))
        for path in source.rglob("*.py")
        if path.name not in ("approve_plan.py", "task.py") and "Event.APPROVED" in path.read_text()
    ]

    assert {name: count for name, count in calls.items() if count} == {"cli.py": 1}
    assert event_users == []


def test_the_implementation_prompt_says_the_agent_cannot_run_commands_and_the_factory_runs_the_tests():
    prompt = build_implementation_prompt("g", "p")

    assert "cannot run commands" in prompt and "factory will run the tests" in prompt


def test_the_implementation_prompt_says_failing_tests_may_be_sent_back():
    from factory.approve_plan import build_implementation_prompt

    assert "may send back test failures" in build_implementation_prompt("g", "p")


# Characterisation (plan 24 C0; these start green): the prompt text and the rejected-review path.
def test_guard_the_implementation_prompt_is_pinned_literally():
    assert build_implementation_prompt("g", "p") == (
        "Implement this goal by following the approved plan.\n\nGOAL: g\n\nPLAN:\np\n\nRules: write the failing test "
        "first, then the implementation. Only add tests; never edit or remove existing tests, and do not change test "
        "configuration (conftest.py, pytest settings). You cannot run commands: the factory will run the tests after "
        "you finish and may send back test failures to fix, so write code you are confident is correct. "
        "Do not use git; do not commit."
    )


def test_guard_a_review_rejected_task_with_findings_cannot_be_approved(store):
    store.save(replace(store.load("t1"), state=TaskState.FAILED, failure_reason="review_rejected",
                       review_findings=("the plan is wrong",)))

    with pytest.raises(InvalidTransition):
        approve_plan("t1", store, plan_digest(PLAN))

    assert store.load("t1").prompt == ""
