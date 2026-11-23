from dataclasses import dataclass, replace
from pathlib import Path

from factory.store import TaskStore
from factory.task import Event, Task, TaskState, apply
from factory.worktrees import (
    WorktreeGit,
    WorktreeManager,
    WorktreeMismatch,
    branch_problem,
    is_ancestor,
    main_checkout_problem,
    run_git,
    worktree_git,
)

MAIN_MOVED = "main moved since the plan; re-run the task"
ALREADY_IN_MAIN = "the audited commit is already in main; there is nothing to merge"


class MergeRefused(Exception):
    pass


def can_merge(task: Task) -> bool:
    """The task-only part of the rules; what needs git (branch, worktree, main) is checked by the preview and the job."""
    return task.state is TaskState.AUDITED and not task.fix_in_flight


class WorktreeMissing(FileNotFoundError):
    """The task has no worktree to check; an `OSError`, like a worktree directory that is gone."""


@dataclass(frozen=True)
class Preview:
    task: Task
    branch: str
    diffstat: str
    findings: tuple[str, ...]
    tool_config: tuple[str, ...] = ()


def merge_preview(task_id: str, store: TaskStore, repo: Path, main_branch: str | None = None) -> Preview:
    task = store.load(task_id)
    apply(task, Event.MERGED)
    branch = f"factory/{task.id}"
    if run_git(repo, "rev-parse", branch) != task.audited_sha:
        raise MergeRefused(f"{branch} is not at the audited commit")
    if task.worktree is None:
        raise WorktreeMissing(f"{task.id} has no worktree")
    tree = _pinned_worktree(task, repo)
    if tree.git("rev-parse", "HEAD") != task.audited_sha:
        raise MergeRefused("the worktree is not at the audited commit")
    if tree.git("status", "--porcelain"):
        raise MergeRefused("the worktree is dirty")
    if problem := main_checkout_problem(repo, main_branch):
        raise MergeRefused(problem)
    main_head = run_git(repo, "rev-parse", "HEAD")
    if main_head not in (task.base_sha, task.audited_sha):  # at the audited commit: a crashed fast-forward, merge_task finishes it
        if is_ancestor(repo, task.audited_sha, main_head):
            raise MergeRefused(ALREADY_IN_MAIN)
        raise MergeRefused(MAIN_MOVED)
    diffstat = run_git(repo, "diff", "--stat=200", task.base_sha, task.audited_sha)
    return Preview(task, branch, diffstat, task.audit_findings, _tool_config_touched(repo, task))


_TOOL_CONFIG_FILES = (".mcp.json", "mise.toml", ".mise.toml")
_TOOL_CONFIG_DIRS = ((".claude",), (".vscode",), (".github", "workflows"))


def _tool_config_touched(repo: Path, task: Task) -> tuple[str, ...]:
    """Root files that tools on the user's machine read and may run after the merge (D7: allowed, but shown)."""
    # -z: raw names, so a quoted name cannot hide a match; casefolded, as a case-insensitive checkout would read them.
    names = run_git(repo, "diff", "--name-only", "-z", "--no-renames", task.base_sha, task.audited_sha).split("\0")
    return tuple(name for name in names if name and _is_tool_config(name.casefold().split("/")))


def _is_tool_config(parts: list[str]) -> bool:
    return "/".join(parts) in _TOOL_CONFIG_FILES or any(tuple(parts[: len(d)]) == d for d in _TOOL_CONFIG_DIRS)


def _pinned_worktree(task: Task, repo: Path) -> WorktreeGit:
    worktree = Path(task.worktree)
    if not worktree.is_dir():
        raise WorktreeMissing(f"{task.id}'s worktree is gone")
    try:
        return worktree_git(worktree, task.id, repo)
    except WorktreeMismatch:  # its message quotes agent-written text: keep it out of the refusal
        raise MergeRefused("the worktree's .git does not point at its own gitdir") from None


def merge_task(
    task_id: str, store: TaskStore, repo: Path, worktrees: WorktreeManager, shown_sha: str, main_branch: str | None = None,
) -> Task:
    """Human-only by process: fast-forward main to the audited commit the human was shown."""
    task = store.load(task_id)
    if shown_sha != task.audited_sha:
        raise MergeRefused("the commit shown is not the audited commit")
    if main_branch is not None and (problem := branch_problem(repo, main_branch)):
        raise MergeRefused(problem)
    if _already_fast_forwarded(task, repo):
        return _record_merged(task, store, worktrees)
    preview = merge_preview(task_id, store, repo, main_branch)
    run_git(repo, "-c", "core.hooksPath=/dev/null", "merge", "--ff-only", task.audited_sha)
    return _record_merged(preview.task, store, worktrees)


def _already_fast_forwarded(task: Task, repo: Path) -> bool:
    """An ff happened but the save did not (a crash): main and the branch are both at the audited commit."""
    apply(task, Event.MERGED)
    return run_git(repo, "rev-parse", "HEAD") == task.audited_sha == run_git(repo, "rev-parse", f"factory/{task.id}")


def _record_merged(task: Task, store: TaskStore, worktrees: WorktreeManager) -> Task:
    merged = apply(task, Event.MERGED)
    store.save(merged)
    try:
        worktrees.remove(task.id)
    except Exception:  # the merge is the fact; a leftover worktree stays visible in task.worktree
        return merged
    cleaned = replace(merged, worktree=None)
    store.save(cleaned)
    return cleaned
