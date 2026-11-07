from dataclasses import dataclass, replace
from pathlib import Path

from factory.store import TaskStore
from factory.task import Event, Task, apply
from factory.worktrees import run_git

_IDENTITY = ("-c", "user.name=factory", "-c", "user.email=factory@localhost")


class RebaseRefused(Exception):
    pass


@dataclass(frozen=True)
class Rebased:
    task: Task
    old_audited_sha: str


def rebase_task(task_id: str, store: TaskStore, repo: Path) -> Rebased:
    """Human-only by process: replay an audited task on the current main, then send it back through verify and audit."""
    task = store.load(task_id)
    apply(task, Event.REBASED)
    worktree = Path(task.worktree)
    main_head = run_git(repo, "rev-parse", "HEAD")
    run_git(worktree, "-c", "commit.gpgsign=false", *_IDENTITY, "rebase", "--onto", main_head, task.base_sha)
    rebased = replace(
        apply(task, Event.REBASED), base_sha=main_head, audited_sha=None, audit_findings=(), audit_error=None,
        fix_rounds_used=0, fix_in_flight=False, fix_frozen_sha=None,
    )
    store.save(rebased)
    return Rebased(rebased, task.audited_sha)
