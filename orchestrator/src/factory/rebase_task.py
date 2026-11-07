import subprocess
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


def _require_rebasable(task: Task, repo: Path, main_head: str) -> Path:
    branch = f"factory/{task.id}"
    if task.fix_in_flight:
        raise RebaseRefused(f"{task.id} has a fix agent that may still be alive: run `factory advance` first")
    if not task.worktree or not Path(task.worktree).is_dir():
        raise RebaseRefused(f"{task.id} has no worktree on disk to rebase in")
    worktree = Path(task.worktree)
    if run_git(worktree, "status", "--porcelain"):
        raise RebaseRefused("the worktree is dirty")
    if run_git(worktree, "rev-parse", "--symbolic-full-name", "HEAD") != f"refs/heads/{branch}":
        raise RebaseRefused(f"the worktree is not on {branch}")
    if run_git(worktree, "rev-parse", "HEAD") != task.audited_sha:
        raise RebaseRefused("the worktree is not at the audited commit")
    if run_git(repo, "rev-parse", branch) != task.audited_sha:
        raise RebaseRefused(f"{branch} is not at the audited commit")
    if main_head == task.base_sha:
        raise RebaseRefused("main has not moved: use `factory merge`")
    if _is_ancestor(repo, task.audited_sha, main_head):
        raise RebaseRefused("the audited commit is already in main")
    return worktree


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    try:
        run_git(repo, "merge-base", "--is-ancestor", ancestor, descendant)
    except subprocess.CalledProcessError:
        return False
    return True


def rebase_task(task_id: str, store: TaskStore, repo: Path) -> Rebased:
    """Human-only by process: replay an audited task on the current main, then send it back through verify and audit."""
    task = store.load(task_id)
    apply(task, Event.REBASED)
    main_head = run_git(repo, "rev-parse", "HEAD")
    worktree = _require_rebasable(task, repo, main_head)
    run_git(worktree, "-c", "commit.gpgsign=false", *_IDENTITY, "rebase", "--onto", main_head, task.base_sha)
    rebased = replace(
        apply(task, Event.REBASED), base_sha=main_head, audited_sha=None, audit_findings=(), audit_error=None,
        fix_rounds_used=0, fix_in_flight=False, fix_frozen_sha=None,
    )
    store.save(rebased)
    return Rebased(rebased, task.audited_sha)
