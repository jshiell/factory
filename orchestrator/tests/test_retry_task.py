from datetime import datetime

import pytest

from factory.retry_task import RetryRefused, retry_task
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
