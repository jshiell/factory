from factory import failure_codes
from factory.store import TaskStore
from factory.task import Event, Task, TaskState, apply


class AbandonRefused(Exception):
    pass


def _refusal(task: Task) -> str | None:
    if task.state in (TaskState.MERGED, TaskState.FAILED):
        return f"{task.id} is {task.state.value}, already finished"
    if task.state is TaskState.RUNNING:
        return f"{task.id} is running: use `factory recover` once the driver is gone"
    if task.fix_in_flight:
        return f"{task.id} has a fix agent that may still be alive: run `factory advance` first"
    return None


def can_abandon(task: Task) -> bool:
    return _refusal(task) is None


def _abandoned(task: Task) -> Task:
    refusal = _refusal(task)
    if refusal:
        raise AbandonRefused(refusal)
    return apply(task, Event.ABANDONED, reason=failure_codes.ABANDONED)


def abandon_task(task_id: str, store: TaskStore) -> Task:
    return store.update(task_id, _abandoned)
