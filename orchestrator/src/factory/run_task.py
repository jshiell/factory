from dataclasses import replace
from typing import Protocol

from factory.command import RunnerConfig
from factory.ledger import Ledger
from factory.step import Runner, preflight, step
from factory.store import TaskStore
from factory.task import Task


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
    path, base_sha = worktrees.create(task_id)
    store.save(replace(store.load(task_id), worktree=str(path), base_sha=base_sha))
    return step(task_id, store, ledger, runner, config, cap_usd)
