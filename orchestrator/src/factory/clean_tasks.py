import subprocess
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
    outside_root: bool = False


def clean_candidates(store: TaskStore, repo: Path, worktree_root: Path) -> list[Candidate]:
    candidates = (_describe(task, repo, worktree_root) for task in store.list().tasks if task.state in CLEANABLE)
    return [c for c in candidates if c.task.worktree or (c.task.state is TaskState.MERGED and c.branch_exists)]


def _describe(task: Task, repo: Path, worktree_root: Path) -> Candidate:
    branch = f"factory/{task.id}"
    branch_exists = bool(run_git(repo, "branch", "--list", branch))
    directory = Path(task.worktree) if task.worktree else None
    outside = directory is not None and not directory.resolve().is_relative_to(worktree_root.resolve())
    if outside:
        directory = None  # never inspect or touch a path the factory did not create
    exists = directory is not None and directory.is_dir()
    dirty = exists and bool(run_git(directory, "status", "--porcelain"))
    return Candidate(task, exists, dirty, _commits_ahead(task, branch, branch_exists, repo), branch_exists, outside)


def _commits_ahead(task: Task, branch: str, branch_exists: bool, repo: Path) -> int:
    if not (branch_exists and task.base_sha):
        return 0
    try:
        return int(run_git(repo, "rev-list", "--count", f"{task.base_sha}..{branch}"))
    except subprocess.CalledProcessError:  # an unknown base commit must not break the preview
        return 0
