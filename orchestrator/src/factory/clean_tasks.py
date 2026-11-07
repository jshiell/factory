import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path

from factory.store import TaskStore
from factory.task import Task, TaskState
from factory.worktrees import WorktreeManager, run_git

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


@dataclass
class Outcome:
    task_id: str
    done: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def clean_tasks(
    candidates: list[Candidate], store: TaskStore, worktrees: WorktreeManager, repo: Path, worktree_root: Path
) -> list[Outcome]:
    return [_clean_one(candidate, store, worktrees, repo) for candidate in candidates]


def _clean_one(candidate: Candidate, store: TaskStore, worktrees: WorktreeManager, repo: Path) -> Outcome:
    task = candidate.task
    outcome = Outcome(task.id)
    if task.worktree:
        if candidate.directory_exists:
            worktrees.remove(task.id)
            outcome.done.append("removed worktree")
        else:
            run_git(repo, "worktree", "prune")
            outcome.done.append("pruned missing worktree")
        store.save(replace(task, worktree=None))
    if task.state is TaskState.MERGED and candidate.branch_exists:
        _delete_branch(task, repo, outcome)
    return outcome


def _delete_branch(task: Task, repo: Path, outcome: Outcome) -> None:
    """`-d` compares against the HEAD of the main checkout, so only try it while main is on a branch."""
    if not run_git(repo, "branch", "--show-current"):
        outcome.problems.append(f"branch factory/{task.id} kept: main is not on a branch")
        return
    try:
        run_git(repo, "branch", "-d", f"factory/{task.id}")
    except subprocess.CalledProcessError:
        outcome.problems.append(f"branch factory/{task.id} kept: git refused to delete it (not merged into main)")
        return
    outcome.done.append("deleted branch")
