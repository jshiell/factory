from dataclasses import dataclass
from pathlib import Path

from factory.agent_call import Infra, call_agent
from factory.classify import OutcomeKind
from factory.command import RunnerConfig
from factory.ledger import Ledger
from factory.step import Runner
from factory.task import Task
from factory.worktree_fingerprint import worktree_fingerprint
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
    repo: Path | None, require_clean: bool = True,
) -> Modified | Infra | Ran:
    """With `require_clean` False the worktree may start dirty; it must then end exactly as it began (`worktree_fingerprint`)."""
    tree = _verified(task, repo)
    if tree is None:
        return Modified()
    head = tree.git("rev-parse", "HEAD")
    if require_clean and _dirty(tree):
        return Modified()
    before = None if require_clean else worktree_fingerprint(tree)
    called = call_agent(task, stage_prompt, runner, config, ledger, stage)
    if isinstance(called, Infra):
        return called
    outcome = called.outcome
    if _verified(task, repo) is None or tree.git("rev-parse", "HEAD") != head:
        return Modified()
    if _dirty(tree) if require_clean else worktree_fingerprint(tree) != before:
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
