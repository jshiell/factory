from dataclasses import replace

import pytest

from gitutil import git

from factory.merge_task import MergeRefused, merge_preview
from factory.store import TaskStore
from factory.task import InvalidTransition, Task, TaskState
from factory.worktrees import WorktreeManager


@pytest.fixture
def audited(tmp_path, repo):
    """An audited task t1: a worktree branch one commit ahead of main."""
    (tmp_path / "tasks").mkdir()
    store = TaskStore(tmp_path / "tasks")
    manager = WorktreeManager(repo, tmp_path / "wt")
    worktree, base = manager.create("t1")
    (worktree / "b.txt").write_text("b")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "Task t1")
    tip = git(worktree, "rev-parse", "HEAD")
    task = Task(id="t1", prompt="p", state=TaskState.AUDITED, worktree=str(worktree), base_sha=base,
                audited_sha=tip, audit_findings=("looks fine",))
    store.save(task)
    return store, repo, worktree, task, manager


def test_preview_of_an_audited_task_names_the_branch_diffstat_and_findings(audited):
    store, repo, _, _, _ = audited

    preview = merge_preview("t1", store, repo)

    assert preview.branch == "factory/t1"
    assert "b.txt" in preview.diffstat
    assert preview.findings == ("looks fine",)


def test_preview_refuses_a_task_that_is_not_audited(audited):
    store, repo, _, task, _ = audited
    store.save(replace(task, state=TaskState.VERIFIED))

    with pytest.raises(InvalidTransition):
        merge_preview("t1", store, repo)


def test_preview_refuses_when_the_branch_tip_is_not_the_audited_sha(audited):
    store, repo, worktree, _, _ = audited
    (worktree / "c.txt").write_text("c")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "extra")

    with pytest.raises(MergeRefused, match="audited"):
        merge_preview("t1", store, repo)


def test_preview_refuses_a_dirty_worktree(audited):
    store, repo, worktree, _, _ = audited
    (worktree / "b.txt").write_text("changed")

    with pytest.raises(MergeRefused, match="dirty"):
        merge_preview("t1", store, repo)


def test_preview_refuses_when_main_moved_since_the_plan(audited):
    store, repo, _, _, _ = audited
    (repo / "m.txt").write_text("m")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "main moved")

    with pytest.raises(MergeRefused, match="moved"):
        merge_preview("t1", store, repo)


def test_preview_refuses_when_main_was_reset_backwards(audited):
    store, repo, _, task, _ = audited
    git(repo, "commit", "-q", "--allow-empty", "-m", "ahead")
    store.save(replace(task, base_sha=git(repo, "rev-parse", "HEAD")))
    git(repo, "reset", "-q", "--hard", "HEAD~1")

    with pytest.raises(MergeRefused, match="moved"):
        merge_preview("t1", store, repo)
