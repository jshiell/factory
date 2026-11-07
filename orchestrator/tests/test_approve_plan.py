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


def test_tripwire_approval_is_only_wired_in_approve_plan():
    """A tripwire, not a guarantee: 'human-only' is enforced by process (see plan 4)."""
    source = Path(__file__).parent.parent / "src" / "factory"
    offenders = [
        path.name
        for path in source.glob("*.py")
        if path.name not in ("approve_plan.py", "task.py")
        and re.search(r"Event\.APPROVED|approve_plan\(", path.read_text())
    ]

    assert offenders == []
