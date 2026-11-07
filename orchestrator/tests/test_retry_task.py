from datetime import datetime

import pytest

from factory.approve_plan import build_implementation_prompt
from factory.digest import plan_digest
from factory.retry_task import RetryRefused, retry_task, retry_with_plan
from factory.store import TaskStore
from factory.task import Task, TaskState

NOW = datetime(2026, 1, 2, 3, 4, 5)


@pytest.fixture
def store(tmp_path):
    (tmp_path / "tasks").mkdir()
    return TaskStore(tmp_path / "tasks")


def put(store, state, task_id="t1", **fields):
    task = Task(id=task_id, prompt="", state=state, **fields)
    store.save(task)
    return task


@pytest.mark.parametrize("state", [s for s in TaskState if s not in (TaskState.FAILED, TaskState.AUDITED)])
def test_retry_refuses_a_task_that_is_not_failed_or_audited(store, state):
    put(store, state, goal="g")

    with pytest.raises(RetryRefused, match=state.value):
        retry_task("t1", store, lambda: NOW)


@pytest.mark.parametrize("state", [TaskState.FAILED, TaskState.AUDITED])
def test_retry_refuses_a_task_with_no_goal(store, state):
    put(store, state)

    with pytest.raises(RetryRefused, match="goal"):
        retry_task("t1", store, lambda: NOW)


def test_retrying_a_failed_task_creates_a_proposed_task_with_the_goal_and_leaves_the_old_one(store):
    old = put(store, TaskState.FAILED, goal="add multiply", failure_reason="tests_failed")

    new = retry_task("t1", store, lambda: NOW)

    assert (new.state, new.goal, new.retry_of) == (TaskState.PROPOSED, "add multiply", "t1")
    assert new.id == "20260102-030405"
    assert store.load(new.id) == new and store.load("t1") == old


def test_an_audited_task_can_be_retried_too(store):
    put(store, TaskState.AUDITED, goal="g")

    assert retry_task("t1", store, lambda: NOW).retry_of == "t1"


def test_two_retries_in_the_same_second_get_distinct_ids(store):
    put(store, TaskState.FAILED, goal="g")

    first = retry_task("t1", store, lambda: NOW)
    second = retry_task("t1", store, lambda: NOW)

    assert (first.id, second.id) == ("20260102-030405", "20260102-030405-2")


PLAN = "1. add it"


def put_approved(store, state=TaskState.FAILED, task_id="t1", **fields):
    """What approve_plan leaves behind: plan, digest, review and the approved prompt."""
    fields = {"goal": "add power", "plan": PLAN, "plan_digest": plan_digest(PLAN), "plan_risks": ("r",),
              "review_findings": ("f",), **fields}
    task = Task(id=task_id, prompt=build_implementation_prompt(fields["goal"], fields["plan"]), state=state, **fields)
    store.save(task)
    return task


def test_keeping_the_plan_queues_a_new_task_with_the_approved_plan_and_leaves_the_old_one(store):
    old = put_approved(store, failure_reason="tests_failed", worktree="/w", base_sha="abc", fix_rounds_used=2)

    new = retry_with_plan("t1", store, lambda: NOW, plan_digest(PLAN))

    assert new.state is TaskState.QUEUED and new.retry_of == "t1" and new.id == "20260102-030405"
    assert (new.goal, new.plan, new.plan_risks, new.plan_digest, new.review_findings) == (
        "add power", PLAN, ("r",), plan_digest(PLAN), ("f",))
    assert new.prompt == build_implementation_prompt("add power", PLAN)
    assert (new.worktree, new.base_sha, new.failure_reason, new.fix_rounds_used) == (None, None, None, 0)
    assert store.load(new.id) == new and store.load("t1") == old
