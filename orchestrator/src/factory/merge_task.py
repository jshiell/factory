from dataclasses import dataclass
from pathlib import Path

from factory.store import TaskStore
from factory.task import Event, Task, apply
from factory.worktrees import run_git


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
    if run_git(repo, "rev-parse", "HEAD") != task.base_sha:
        raise MergeRefused("main moved since the plan; re-run the task")
    diffstat = run_git(repo, "diff", "--stat=200", task.base_sha, task.audited_sha)
    return Preview(task, branch, diffstat, task.audit_findings)
