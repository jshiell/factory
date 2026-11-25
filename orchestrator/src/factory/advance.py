import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from factory.gate import ProjectSubdirEscapes
from factory.run_task import WorktreeNotPristine
from factory.step import BudgetCapReached, MissingBudget
from factory.store import TaskStore
from factory.task import InvalidTransition, Task, TaskState
from factory.textsafe import log_failure
from factory.worktrees import BranchExists, WorktreeExists

ACTIONS = {
    TaskState.PROPOSED: "plan",
    TaskState.PLANNED: "review",
    TaskState.QUEUED: "implement",
    TaskState.AGENT_DONE: "verify",
    TaskState.VERIFIED: "audit",
}

def can_advance(task: Task) -> bool:
    return task.state in ACTIONS


_KNOWN_ERRORS = (
    BudgetCapReached, MissingBudget, InvalidTransition, WorktreeExists, BranchExists,
    ProjectSubdirEscapes,
)


@dataclass(frozen=True)
class Step:
    stage: str
    state: TaskState


@dataclass(frozen=True)
class StageError:
    field: str
    code: str


@dataclass(frozen=True)
class AdvanceResult:
    task: Task
    steps: list[Step]
    stopped: str | None = None
    stopped_error: StageError | None = None  # the recorded error behind a no-progress stop, kept raw for display


def _recorded_error(task: Task) -> StageError | None:
    for field in ("plan_error", "review_error", "audit_error"):
        if code := getattr(task, field):
            return StageError(field, code)
    return None


def advance(
    task_id: str, store: TaskStore, stages: dict[str, Callable[[str], Task]], max_steps: int = 10
) -> AdvanceResult:
    task = store.load(task_id)
    steps: list[Step] = []
    while True:
        if task.state is TaskState.RUNNING:
            return AdvanceResult(task, steps, "task is running but no driver holds the lock: run `factory recover` (a recorded agent was just killed)")
        stage = ACTIONS.get(task.state)
        if stage is None:
            return AdvanceResult(task, steps)
        if len(steps) >= max_steps:
            return AdvanceResult(task, steps, f"step cap ({max_steps}) reached")
        try:
            after = stages[stage](task_id)
        except WorktreeNotPristine:
            return AdvanceResult(task, steps, f"{stage}: needs human action: worktree dirty or moved")
        except subprocess.CalledProcessError as error:  # the command line and git's output stay in the log
            log_failure(f"advance {task_id} {stage}", error)
            return AdvanceResult(store.load(task_id), steps, f"{stage}: CalledProcessError: git failed, see the server log")
        except _KNOWN_ERRORS as error:
            return AdvanceResult(store.load(task_id), steps, f"{stage}: {type(error).__name__}: {error}")
        if after.state is task.state:
            error = _recorded_error(after)
            return AdvanceResult(after, steps, f"{stage}: no progress ({error.code if error else 'unknown'})", error)
        steps.append(Step(stage, after.state))
        task = after
