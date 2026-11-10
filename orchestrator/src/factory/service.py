from collections import defaultdict
from typing import NamedTuple

from collections.abc import Callable
from datetime import datetime

from factory.store import Listing, create_with_fresh_id
from factory.task import Task, TaskState


class CostReport(NamedTuple):
    by_stage: dict[str, float]
    by_task: dict[str, float]
    assumed: float
    total: float


def create_task(store, clock: Callable[[], datetime], goal: str) -> Task:
    return create_with_fresh_id(
        store, clock, lambda task_id: Task(id=task_id, prompt="", state=TaskState.PROPOSED, goal=goal)
    )


def list_tasks(store) -> Listing:
    return store.list()


def get_task(store, task_id: str) -> Task:
    return store.load(task_id)


def cost_report(store, ledger, task_id: str | None = None) -> CostReport:
    """Spend by stage and by task, for the whole ledger or one task (which must exist)."""
    if task_id is not None:
        store.load(task_id)
    entries = [e for e in ledger.entries() if task_id in (None, e["task_id"])]
    by_stage, by_task = defaultdict(float), defaultdict(float)
    for entry in entries:
        by_stage[entry.get("stage", "unknown")] += entry["cost_usd"]
        by_task[entry["task_id"]] += entry["cost_usd"]
    assumed = sum(e["cost_usd"] for e in entries if e.get("assumed"))
    return CostReport(dict(by_stage), dict(by_task), assumed, sum(e["cost_usd"] for e in entries))
