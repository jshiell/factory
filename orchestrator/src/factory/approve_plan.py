from dataclasses import replace

from factory.digest import plan_digest
from factory.store import TaskStore
from factory.task import Event, Task, apply


def build_implementation_prompt(goal: str, plan: str) -> str:
    return (
        f"Implement this goal by following the approved plan.\n\nGOAL: {goal}\n\nPLAN:\n{plan}\n\n"
        "Rules: write the failing test first, then the implementation. Only add tests; never edit or "
        "remove existing tests, and do not change test configuration (conftest.py, pytest settings). "
        "You cannot run commands: the factory will run the tests after you finish and may send back test failures to fix, so write code you are confident is correct. "
        "Do not use git; do not commit."
    )


def approve_plan(task_id: str, store: TaskStore, shown_digest: str) -> Task:
    """Human-only by process: queue a reviewed task whose plan matches the digest the human was shown."""
    task = store.load(task_id)
    approved = apply(task, Event.APPROVED)
    actual = plan_digest(task.plan)
    if not (actual == shown_digest == task.plan_digest):
        raise ValueError(f"plan digest mismatch for {task_id}")
    queued = replace(approved, prompt=build_implementation_prompt(task.goal, task.plan))
    store.save(queued)
    return queued


def reject_plan(task_id: str, store: TaskStore) -> Task:
    rejected = apply(store.load(task_id), Event.HUMAN_REJECTED, "plan_rejected_by_human")
    store.save(rejected)
    return rejected
