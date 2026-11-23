import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from factory.command import RunnerConfig
from factory.ledger import Ledger
from factory.step import Runner, preflight, step
from factory.store import TaskStore
from factory.task import Task
from factory.worktrees import WorktreeMismatch, worktree_git


class WorktreeNotPristine(Exception):
    pass


class WorktreeFactory(Protocol):
    def create(self, task_id: str): ...


def run_task(
    task_id: str,
    store: TaskStore,
    ledger: Ledger,
    runner: Runner,
    config: RunnerConfig,
    cap_usd: float,
    worktrees: WorktreeFactory,
    repo: Path | None,
) -> Task:
    preflight(task_id, store, ledger, config, cap_usd)
    task = store.load(task_id)
    if task.worktree is not None:
        _require_pristine(task, repo)
    else:
        path, base_sha = worktrees.create(task_id)
        store.save(replace(task, worktree=str(path), base_sha=base_sha))
    return step(task_id, store, ledger, runner, config, cap_usd)


def _require_pristine(task: Task, repo: Path | None) -> None:
    """A pre-made worktree (from the plan stage) must still be at base_sha and clean, with its own `.git`."""
    try:
        tree = worktree_git(Path(task.worktree), task.id, repo)
        pristine = tree.git("rev-parse", "HEAD") == task.base_sha and not tree.git("status", "--porcelain")
    except (OSError, subprocess.CalledProcessError, WorktreeMismatch):
        pristine = False
    if not pristine:
        raise WorktreeNotPristine(task.id)
