from dataclasses import replace

import pytest

from gitutil import git

from factory.rebase_task import RebaseRefused, rebase_task
from factory.store import TaskStore
from factory.task import Task, TaskState
from factory.worktrees import WorktreeManager


@pytest.fixture
def audited(tmp_path, repo):
    """An audited task t1 (b.txt added) whose base main has since moved past (m.txt added on main)."""
    (tmp_path / "tasks").mkdir()
    store = TaskStore(tmp_path / "tasks")
    manager = WorktreeManager(repo, tmp_path / "wt")
    worktree, base = manager.create("t1")
    (worktree / "b.txt").write_text("b")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "Task t1")
    tip = git(worktree, "rev-parse", "HEAD")
    task = Task(id="t1", prompt="p", state=TaskState.AUDITED, worktree=str(worktree), base_sha=base, goal="g", plan="pl",
                audited_sha=tip, audit_findings=("fine",), audit_error="old", fix_rounds_used=2, fix_frozen_sha=base)
    store.save(task)
    (repo / "m.txt").write_text("m")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "main moved")
    return store, repo, worktree, task


def test_a_clean_rebase_replays_the_task_on_main_and_sends_it_back_to_agent_done(audited):
    store, repo, worktree, before = audited
    main_head = git(repo, "rev-parse", "HEAD")

    result = rebase_task("t1", store, repo)

    after = store.load("t1")
    assert after.state is TaskState.AGENT_DONE and after.base_sha == main_head
    assert git(worktree, "rev-parse", "HEAD") == git(repo, "rev-parse", "factory/t1") != before.audited_sha
    assert git(worktree, "merge-base", "--is-ancestor", main_head, "HEAD") == ""
    assert (worktree / "b.txt").exists() and (worktree / "m.txt").exists()
    assert (after.audited_sha, after.audit_findings, after.audit_error) == (None, (), None)
    assert (after.fix_rounds_used, after.fix_in_flight, after.fix_frozen_sha) == (0, False, None)
    assert replace(after, state=before.state, base_sha=before.base_sha, audited_sha=before.audited_sha,
                   audit_findings=before.audit_findings, audit_error=before.audit_error,
                   fix_rounds_used=before.fix_rounds_used, fix_frozen_sha=before.fix_frozen_sha) == before
    assert result.old_audited_sha == before.audited_sha
