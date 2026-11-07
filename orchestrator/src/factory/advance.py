import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from factory.gate import ProjectSubdirEscapes
from factory.run_task import WorktreeNotPristine
from factory.step import BudgetCapReached, MissingBudget
from factory.store import TaskStore
from factory.task import InvalidTransition, Task, TaskState
from factory.worktrees import BranchExists, WorktreeExists

ACTIONS = {
    TaskState.PROPOSED: "plan",
    TaskState.PLANNED: "review",
    TaskState.QUEUED: "implement",
    TaskState.AGENT_DONE: "verify",
    TaskState.VERIFIED: "audit",
}

_KNOWN_ERRORS = (
    BudgetCapReached, MissingBudget, InvalidTransition, WorktreeExists, BranchExists,
    subprocess.CalledProcessError, ProjectSubdirEscapes,
)


@dataclass(frozen=True)
class Step:
    stage: str
    state: TaskState


@dataclass(frozen=True)
class AdvanceResult:
    task: Task
    steps: list[Step]
    stopped: str | None = None


def advance(
    task_id: str, store: TaskStore, stages: dict[str, Callable[[str], Task]], max_steps: int = 10
) -> AdvanceResult:
    task = store.load(task_id)
    steps: list[Step] = []
    while True:
        if task.state is TaskState.RUNNING:
            return AdvanceResult(task, steps, "task is running: no driver holds the lock; the agent may still be alive")
        stage = ACTIONS.get(task.state)
        if stage is None:
            return AdvanceResult(task, steps)
        if len(steps) >= max_steps:
            return AdvanceResult(task, steps, f"step cap ({max_steps}) reached")
        try:
            after = stages[stage](task_id)
        except WorktreeNotPristine:
            return AdvanceResult(task, steps, f"{stage}: needs human action: worktree dirty or moved")
        except _KNOWN_ERRORS as error:
            return AdvanceResult(store.load(task_id), steps, f"{stage}: {type(error).__name__}: {error}")
        if after.state is task.state:
            reason = after.plan_error or after.review_error or after.audit_error or "unknown"
            return AdvanceResult(after, steps, f"{stage}: no progress ({reason})")
        steps.append(Step(stage, after.state))
        task = after
