from factory.ledger import Ledger
from factory.step import ASSUMED_COST_MULTIPLIER
from factory.store import TaskStore
from factory.task import Event, Task, TaskState, apply


class RecoverRefused(Exception):
    pass


def can_recover(task: Task) -> bool:
    return task.state is TaskState.RUNNING


def recover_task(task_id: str, store: TaskStore, ledger: Ledger, budget_usd: float) -> Task:
    task = store.load(task_id)
    if not can_recover(task):
        raise RecoverRefused(f"{task.id} is {task.state.value}, not running")
    ledger.append(task.id, ASSUMED_COST_MULTIPLIER * budget_usd, assumed=True, stage="implement")
    failed = apply(task, Event.FAILED, reason="interrupted")
    store.save(failed)
    return failed
