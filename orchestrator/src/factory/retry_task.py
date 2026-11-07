from datetime import datetime
from collections.abc import Callable

from factory.store import TaskStore
from factory.task import Task, TaskState

RETRYABLE = (TaskState.FAILED, TaskState.AUDITED)


class RetryRefused(Exception):
    pass


def retry_task(task_id: str, store: TaskStore, clock: Callable[[], datetime]) -> Task:
    task = store.load(task_id)
    if task.state not in RETRYABLE:
        raise RetryRefused(f"{task.id} is {task.state.value}; only failed or audited tasks can be retried")
    if not task.goal:
        raise RetryRefused(f"{task.id} has no goal to retry")
    raise NotImplementedError
