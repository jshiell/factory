import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from factory.command import RunnerConfig
from factory.ledger import Ledger
from factory.step import Runner, preflight, step
from factory.store import TaskStore
from factory.task import Task
from factory.worktrees import run_git


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
) -> Task:
    preflight(task_id, store, ledger, config, cap_usd)
    task = store.load(task_id)
    if task.worktree is not None:
        _require_pristine(task)
    else:
        path, base_sha = worktrees.create(task_id)
        store.save(replace(task, worktree=str(path), base_sha=base_sha))
    return step(task_id, store, ledger, runner, config, cap_usd)


def _require_pristine(task: Task) -> None:
    """A pre-made worktree (from the plan stage) must still be at base_sha and clean."""
    worktree = Path(task.worktree)
    try:
        pristine = run_git(worktree, "rev-parse", "HEAD") == task.base_sha and not run_git(
            worktree, "status", "--porcelain"
        )
    except (OSError, subprocess.CalledProcessError):
        pristine = False
    if not pristine:
        raise WorktreeNotPristine(task.id)
