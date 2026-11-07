from dataclasses import dataclass, replace
from pathlib import Path

from factory.store import TaskStore
from factory.task import Event, Task, apply
from factory.worktrees import WorktreeManager, run_git


class MergeRefused(Exception):
    pass


@dataclass(frozen=True)
class Preview:
    task: Task
    branch: str
    diffstat: str
    findings: tuple[str, ...]


def merge_preview(task_id: str, store: TaskStore, repo: Path) -> Preview:
    task = store.load(task_id)
    apply(task, Event.MERGED)
    branch = f"factory/{task.id}"
    if run_git(repo, "rev-parse", branch) != task.audited_sha:
        raise MergeRefused(f"{branch} is not at the audited commit")
    if run_git(Path(task.worktree), "rev-parse", "HEAD") != task.audited_sha:
        raise MergeRefused("the worktree is not at the audited commit")
    if run_git(Path(task.worktree), "status", "--porcelain"):
        raise MergeRefused("the worktree is dirty")
    if not run_git(repo, "branch", "--show-current"):
        raise MergeRefused("main is not on a branch")
    if run_git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise MergeRefused("main has uncommitted changes")
    if run_git(repo, "rev-parse", "HEAD") != task.base_sha:
        raise MergeRefused("main moved since the plan; re-run the task")
    diffstat = run_git(repo, "diff", "--stat=200", task.base_sha, task.audited_sha)
    return Preview(task, branch, diffstat, task.audit_findings)


def merge_task(task_id: str, store: TaskStore, repo: Path, worktrees: WorktreeManager, shown_sha: str) -> Task:
    """Human-only by process: fast-forward main to the audited commit the human was shown."""
    task = store.load(task_id)
    if shown_sha != task.audited_sha:
        raise MergeRefused("the commit shown is not the audited commit")
    preview = merge_preview(task_id, store, repo)
    run_git(repo, "merge", "--ff-only", task.audited_sha)
    merged = replace(apply(preview.task, Event.MERGED), worktree=None)
    store.save(merged)
    worktrees.remove(task_id)
    return merged
