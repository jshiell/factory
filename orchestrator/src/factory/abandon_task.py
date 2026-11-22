from factory.store import TaskStore
from factory.task import Event, Task, TaskState, apply


class AbandonRefused(Exception):
    pass


def _abandoned(task: Task) -> Task:
    if task.state in (TaskState.MERGED, TaskState.FAILED):
        raise AbandonRefused(f"{task.id} is {task.state.value}, already finished")
    if task.state is TaskState.RUNNING:
        raise AbandonRefused(f"{task.id} is running: use `factory recover` once the driver is gone")
    if task.fix_in_flight:
        raise AbandonRefused(f"{task.id} has a fix agent that may still be alive: run `factory advance` first")
    return apply(task, Event.ABANDONED, reason="abandoned")


def abandon_task(task_id: str, store: TaskStore) -> Task:
    return store.update(task_id, _abandoned)
