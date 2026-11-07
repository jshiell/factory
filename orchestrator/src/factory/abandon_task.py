from factory.store import TaskStore
from factory.task import Event, Task, TaskState, apply


class AbandonRefused(Exception):
    pass


def abandon_task(task_id: str, store: TaskStore) -> Task:
    task = store.load(task_id)
    if task.state in (TaskState.MERGED, TaskState.FAILED):
        raise AbandonRefused(f"{task.id} is {task.state.value}, already finished")
    abandoned = apply(task, Event.ABANDONED, reason="abandoned")
    store.save(abandoned)
    return abandoned
