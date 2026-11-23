from dataclasses import dataclass
from pathlib import Path

from factory.agent_call import Infra, call_agent
from factory.classify import OutcomeKind
from factory.command import RunnerConfig
from factory.ledger import Ledger
from factory.step import Runner
from factory.task import Task
from factory.worktrees import WorktreeGit, WorktreeMismatch, worktree_git


@dataclass(frozen=True)
class Modified:
    """The worktree was dirty before the run, or the agent changed it."""


@dataclass(frozen=True)
class Ran:
    structured_output: dict | None
    head: str


def run_readonly_stage(
    task: Task, stage_prompt: str, runner: Runner, config: RunnerConfig, ledger: Ledger, stage: str,
    repo: Path | None = None,
) -> Modified | Infra | Ran:
    tree = _verified(task, repo)
    if tree is None:
        return Modified()
    head = tree.git("rev-parse", "HEAD")
    if _dirty(tree):
        return Modified()
    called = call_agent(task, stage_prompt, runner, config, ledger, stage)
    if isinstance(called, Infra):
        return called
    outcome = called.outcome
    if _verified(task, repo) is None or tree.git("rev-parse", "HEAD") != head or _dirty(tree):
        return Modified()
    if outcome.kind not in (OutcomeKind.SUCCESS, OutcomeKind.SUCCESS_WITH_DENIALS):
        return Infra(outcome.kind.value)
    return Ran(outcome.structured_output, head)


def _verified(task: Task, repo: Path | None) -> WorktreeGit | None:
    """Git pinned to the task's own gitdir, or None when the worktree's `.git` was rewritten."""
    try:
        return worktree_git(Path(task.worktree), task.id, repo)
    except WorktreeMismatch:
        return None


def _dirty(tree: WorktreeGit) -> bool:
    return bool(tree.git("status", "--porcelain"))
