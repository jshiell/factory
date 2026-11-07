from dataclasses import replace

import pytest

from factory.abandon_task import AbandonRefused, abandon_task
from factory.store import TaskStore
from factory.task import Task, TaskState

ABANDONABLE = [TaskState.PROPOSED, TaskState.PLANNED, TaskState.REVIEWED, TaskState.QUEUED,
               TaskState.AGENT_DONE, TaskState.VERIFIED, TaskState.AUDITED]


@pytest.fixture
def store(tmp_path):
    (tmp_path / "tasks").mkdir()
    return TaskStore(tmp_path / "tasks")


def saved(store, state, **fields):
    task = Task(id="t1", prompt="p", state=state, goal="g", plan="pl", worktree="/wt/t1", base_sha="abc",
                audit_findings=("fine",), **fields)
    store.save(task)
    return task


@pytest.mark.parametrize("state", ABANDONABLE)
def test_each_non_terminal_state_but_running_becomes_failed_abandoned_changing_nothing_else(store, state):
    before = saved(store, state)

    after = abandon_task("t1", store)

    assert after == replace(before, state=TaskState.FAILED, failure_reason="abandoned")
    assert store.load("t1") == after


@pytest.mark.parametrize("state", [TaskState.MERGED, TaskState.FAILED])
def test_a_merged_or_failed_task_is_refused_naming_its_state(store, state):
    saved(store, state)

    with pytest.raises(AbandonRefused, match=state.value):
        abandon_task("t1", store)

    assert store.load("t1").state is state


def test_a_running_task_is_refused_pointing_at_recover(store):
    saved(store, TaskState.RUNNING)

    with pytest.raises(AbandonRefused, match="recover"):
        abandon_task("t1", store)

    assert store.load("t1").state is TaskState.RUNNING


def test_an_agent_done_task_with_a_fix_agent_in_flight_is_refused_pointing_at_advance(store):
    saved(store, TaskState.AGENT_DONE, fix_in_flight=True)

    with pytest.raises(AbandonRefused, match="advance"):
        abandon_task("t1", store)

    assert store.load("t1").state is TaskState.AGENT_DONE
