from dataclasses import replace

import pytest

from factory.abandon_task import can_abandon
from factory.task import Task, TaskState


def task_in(state, **fields):
    return Task(id="t1", prompt="", state=state, goal="g", **fields)


@pytest.mark.parametrize("state", [s for s in TaskState if s not in (TaskState.MERGED, TaskState.FAILED, TaskState.RUNNING)])
def test_can_abandon_an_unfinished_task_that_is_not_running(state):
    assert can_abandon(task_in(state))


@pytest.mark.parametrize("state", [TaskState.MERGED, TaskState.FAILED, TaskState.RUNNING])
def test_cannot_abandon_a_finished_or_running_task(state):
    assert not can_abandon(task_in(state))


def test_cannot_abandon_a_task_with_a_fix_agent_in_flight():
    assert not can_abandon(replace(task_in(TaskState.AGENT_DONE), fix_in_flight=True))
