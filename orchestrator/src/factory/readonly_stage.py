from dataclasses import dataclass
from pathlib import Path

from factory.agent_call import Infra, call_agent
from factory.classify import OutcomeKind
from factory.command import RunnerConfig
from factory.ledger import Ledger
from factory.step import Runner
from factory.task import Task
from factory.worktrees import run_git


@dataclass(frozen=True)
class Modified:
    """The worktree was dirty before the run, or the agent changed it."""


@dataclass(frozen=True)
class Ran:
    structured_output: dict | None
    head: str


def run_readonly_stage(
    task: Task, stage_prompt: str, runner: Runner, config: RunnerConfig, ledger: Ledger, stage: str
) -> Modified | Infra | Ran:
    worktree = Path(task.worktree)
    head = run_git(worktree, "rev-parse", "HEAD")
    if _dirty(worktree):
        return Modified()
    called = call_agent(task, stage_prompt, runner, config, ledger, stage)
    if isinstance(called, Infra):
        return called
    outcome = called.outcome
    if run_git(worktree, "rev-parse", "HEAD") != head or _dirty(worktree):
        return Modified()
    if outcome.kind not in (OutcomeKind.SUCCESS, OutcomeKind.SUCCESS_WITH_DENIALS):
        return Infra(outcome.kind.value)
    return Ran(outcome.structured_output, head)


def _dirty(worktree: Path) -> bool:
    return bool(run_git(worktree, "status", "--porcelain"))
