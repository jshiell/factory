from dataclasses import replace

import pytest

from factory.abandon_task import can_abandon
from factory.advance import ACTIONS, can_advance
from factory.rebase_task import can_rebase
from factory.recover_task import can_recover
from factory.retry_task import RETRYABLE, can_retry
from factory.task import Task, TaskState


def task_in(state, **fields):
    return Task(id="t1", prompt="", state=state, goal="g", **fields)


@pytest.mark.parametrize("state", [s for s in TaskState if s not in (TaskState.MERGED, TaskState.FAILED, TaskState.RUNNING)])
def test_can_abandon_an_unfinished_task_that_is_not_running(state):
    assert can_abandon(task_in(state))


@pytest.mark.parametrize("state", [TaskState.MERGED, TaskState.FAILED, TaskState.RUNNING])
def test_cannot_abandon_a_finished_or_running_task(state):
    assert not can_abandon(task_in(state))


def test_can_recover_only_a_running_task():
    assert [s for s in TaskState if can_recover(task_in(s))] == [TaskState.RUNNING]


def test_can_advance_only_states_with_a_next_stage():
    assert {s for s in TaskState if can_advance(task_in(s))} == set(ACTIONS)


def test_can_retry_failed_or_audited_tasks_with_a_goal():
    assert {s for s in TaskState if can_retry(task_in(s))} == set(RETRYABLE)


def test_cannot_retry_a_task_without_a_goal():
    assert not can_retry(replace(task_in(TaskState.FAILED), goal=""))


def test_cannot_abandon_a_task_with_a_fix_agent_in_flight():
    assert not can_abandon(replace(task_in(TaskState.AGENT_DONE), fix_in_flight=True))


def test_can_rebase_only_an_audited_task():
    assert [s for s in TaskState if can_rebase(task_in(s))] == [TaskState.AUDITED]


def test_cannot_rebase_a_task_with_a_fix_agent_in_flight():
    assert not can_rebase(replace(task_in(TaskState.AUDITED), fix_in_flight=True))
