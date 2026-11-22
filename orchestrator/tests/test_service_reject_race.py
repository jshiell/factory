from dataclasses import replace

import pytest

from factory import service
from factory.sqlite_store import SqliteTaskStore
from factory.task import Task, TaskState


class _MovesToQueuedAfterTheFirstLoad:
    def __init__(self, store):
        self._store, self._loaded = store, False

    def load(self, task_id):
        task = self._store.load(task_id)
        if not self._loaded:
            self._loaded = True
            self._store.save(replace(task, state=TaskState.QUEUED))
        return task

    def update(self, task_id, change):
        return self._store.update(task_id, change)


def test_rejecting_a_plan_that_left_review_after_the_check_raises_not_reviewed_and_changes_nothing(tmp_path):
    inner = SqliteTaskStore(tmp_path / "factory.db")
    inner.save(Task(id="a", prompt="", state=TaskState.REVIEWED, plan="p"))

    with pytest.raises(service.NotReviewed) as raised:
        service.reject_plan(_MovesToQueuedAfterTheFirstLoad(inner), "a")

    assert raised.value.task.state is TaskState.QUEUED
    assert inner.load("a").state is TaskState.QUEUED
