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
    diffstat = run_git(repo, "diff", "--stat=200", task.base_sha, task.audited_sha)
    return Preview(task, f"factory/{task.id}", diffstat, task.audit_findings)
