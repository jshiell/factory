from dataclasses import dataclass, replace
from pathlib import Path

from factory.classify import OutcomeKind, classify
from factory.command import RunnerConfig
from factory.ledger import Ledger
from factory.step import ASSUMED_COST_MULTIPLIER, Runner
from factory.task import Task
from factory.worktrees import run_git


@dataclass(frozen=True)
class Modified:
    """The worktree was dirty before the run, or the agent changed it."""


@dataclass(frozen=True)
class Infra:
    reason: str


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
    try:
        result = runner.run(replace(task, prompt=stage_prompt), config)
    except Exception:
        return _assumed_failure("runner_error", task, config, ledger, stage)
    if result.timed_out:
        return _assumed_failure("timeout", task, config, ledger, stage)
    outcome = classify(result.stdout, result.exit_code)
    ledger.append(task.id, outcome.cost_usd, stage=stage)
    if run_git(worktree, "rev-parse", "HEAD") != head or _dirty(worktree):
        return Modified()
    if outcome.kind not in (OutcomeKind.SUCCESS, OutcomeKind.SUCCESS_WITH_DENIALS):
        return Infra(outcome.kind.value)
    return Ran(outcome.structured_output, head)


def _dirty(worktree: Path) -> bool:
    return bool(run_git(worktree, "status", "--porcelain"))


def _assumed_failure(reason: str, task: Task, config: RunnerConfig, ledger: Ledger, stage: str) -> Infra:
    ledger.append(task.id, ASSUMED_COST_MULTIPLIER * config.budget_usd, assumed=True, stage=stage)
    return Infra(reason)
