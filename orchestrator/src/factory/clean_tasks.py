from dataclasses import dataclass
from pathlib import Path

from factory.store import TaskStore
from factory.task import Task, TaskState
from factory.worktrees import run_git

CLEANABLE = (TaskState.MERGED, TaskState.FAILED)


@dataclass(frozen=True)
class Candidate:
    task: Task
    directory_exists: bool
    dirty: bool
    commits_ahead: int
    branch_exists: bool


def clean_candidates(store: TaskStore, repo: Path, worktree_root: Path) -> list[Candidate]:
    candidates = (_describe(task, repo) for task in store.list().tasks if task.state in CLEANABLE)
    return [c for c in candidates if c.task.worktree or (c.task.state is TaskState.MERGED and c.branch_exists)]


def _describe(task: Task, repo: Path) -> Candidate:
    branch = f"factory/{task.id}"
    branch_exists = bool(run_git(repo, "branch", "--list", branch))
    directory = Path(task.worktree) if task.worktree else None
    exists = directory is not None and directory.is_dir()
    dirty = exists and bool(run_git(directory, "status", "--porcelain"))
    ahead = int(run_git(repo, "rev-list", "--count", f"{task.base_sha}..{branch}")) if branch_exists and task.base_sha else 0
    return Candidate(task, exists, dirty, ahead, branch_exists)
