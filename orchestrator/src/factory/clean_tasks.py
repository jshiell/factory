from dataclasses import dataclass
from pathlib import Path

from factory.store import TaskStore
from factory.task import Task, TaskState
from factory.worktrees import run_git

CLEANABLE = (TaskState.MERGED, TaskState.FAILED)


@dataclass(frozen=True)
class Candidate:
    task: Task


def clean_candidates(store: TaskStore, repo: Path, worktree_root: Path) -> list[Candidate]:
    return [Candidate(task) for task in store.list().tasks if task.state in CLEANABLE and _has_leftovers(task, repo)]


def _has_leftovers(task: Task, repo: Path) -> bool:
    if task.worktree:
        return True
    return task.state is TaskState.MERGED and bool(run_git(repo, "branch", "--list", f"factory/{task.id}"))
