from dataclasses import dataclass
from pathlib import Path

from factory.store import TaskStore
from factory.task import Task, TaskState

CLEANABLE = (TaskState.MERGED, TaskState.FAILED)


@dataclass(frozen=True)
class Candidate:
    task: Task


def clean_candidates(store: TaskStore, repo: Path, worktree_root: Path) -> list[Candidate]:
    return [Candidate(task) for task in store.list().tasks if task.state in CLEANABLE and task.worktree]
