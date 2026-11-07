import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path

from factory.gate_cache import OrphanCache, cache_size, remove_cache
from factory.store import TaskNotFound, TaskStore
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
    cache_bytes: int | None = None  # None: no gate cache directory
    branch_empty: bool = False  # the branch tip is exactly the base commit: nothing on it to lose


def clean_candidates(
    store: TaskStore, repo: Path, worktree_root: Path, cache_root: Path | None = None
) -> list[Candidate]:
    candidates = (
        _describe(task, repo, worktree_root, cache_root) for task in store.list().tasks if task.state in CLEANABLE
    )
    return [
        c for c in candidates
        if c.task.worktree or (c.task.state is TaskState.MERGED and c.branch_exists) or c.cache_bytes is not None
        or (c.task.state is TaskState.FAILED and c.branch_empty)
    ]


def _describe(task: Task, repo: Path, worktree_root: Path, cache_root: Path | None) -> Candidate:
    branch = f"factory/{task.id}"
    branch_exists = bool(run_git(repo, "branch", "--list", branch))
    directory = Path(task.worktree) if task.worktree else None
    outside = directory is not None and directory.resolve() != (worktree_root / task.id).resolve()
    if outside:
        directory = None  # never inspect or touch a path the factory did not create
    exists = directory is not None and directory.is_dir()
    dirty = exists and bool(run_git(directory, "status", "--porcelain"))
    size = None if cache_root is None else cache_size(cache_root, task.id)
    ahead = _commits_ahead(task, branch, branch_exists, repo)
    return Candidate(task, exists, dirty, ahead, branch_exists, outside, size, _branch_empty(task, repo))


def _branch_empty(task: Task, repo: Path) -> bool:
    """Strict: unknown (no base, no branch, any git error) is never empty."""
    if not task.base_sha:
        return False
    try:
        return run_git(repo, "rev-parse", "--verify", "-q", f"refs/heads/factory/{task.id}") == task.base_sha
    except subprocess.CalledProcessError:
        return False


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
    candidates: list[Candidate], store: TaskStore, worktrees: WorktreeManager, repo: Path, worktree_root: Path,
    cache_root: Path | None = None,
) -> list[Outcome]:
    return [_clean_one(candidate, store, worktrees, repo, cache_root) for candidate in candidates]


def _clean_one(
    candidate: Candidate, store: TaskStore, worktrees: WorktreeManager, repo: Path, cache_root: Path | None
) -> Outcome:
    outcome = Outcome(candidate.task.id)
    task = store.load(candidate.task.id)
    if task.state not in CLEANABLE:
        outcome.problems.append(f"skipped: {task.id} is now {task.state.value}")
        return outcome
    if candidate.cache_bytes is not None and cache_root is not None:  # independent of the worktree steps
        problem = remove_cache(cache_root, task.id)  # path recomputed from cache_root/id, never carried
        (outcome.problems if problem else outcome.done).append(problem or "removed gate cache")
    if candidate.outside_root:
        outcome.problems.append(f"skipped: worktree {task.worktree} is outside the worktree root")
        return outcome
    if task.worktree and not _remove_worktree(task, candidate, store, worktrees, repo, outcome):
        return outcome
    if task.state is TaskState.MERGED and candidate.branch_exists:
        _delete_branch(task, repo, outcome)
    elif task.state is TaskState.FAILED and candidate.branch_empty and _branch_empty(task, repo):  # re-checked now
        _delete_branch(task, repo, outcome)
    return outcome


def _remove_worktree(
    task: Task, candidate: Candidate, store: TaskStore, worktrees: WorktreeManager, repo: Path, outcome: Outcome
) -> bool:
    try:
        if candidate.directory_exists:
            worktrees.remove(task.id)
            outcome.done.append("removed worktree")
        else:
            run_git(repo, "worktree", "prune")
            outcome.done.append("pruned missing worktree")
    except subprocess.CalledProcessError as error:
        outcome.problems.append(f"worktree kept: git failed ({error})")
        return False
    store.save(replace(task, worktree=None))
    return True


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


def remove_orphan_caches(store: TaskStore, cache_root: Path, orphans: list[OrphanCache]) -> list[Outcome]:
    """Remove caches no task record claims, re-checking each one first."""
    outcomes = []
    for orphan in orphans:
        outcome = Outcome(orphan.name)
        try:
            store.load(orphan.name)
            outcome.problems.append(f"orphan cache {orphan.name} kept: a task record now exists")
        except TaskNotFound:
            problem = remove_cache(cache_root, orphan.name)
            (outcome.problems if problem else outcome.done).append(problem or "removed orphan gate cache")
        except Exception:
            outcome.problems.append(f"orphan cache {orphan.name} kept: its task record is unreadable")
        outcomes.append(outcome)
    return outcomes
