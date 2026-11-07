from dataclasses import replace

import pytest

from gitutil import git

from factory.merge_task import MergeRefused, merge_preview, merge_task
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


def test_preview_refuses_a_detached_main_head(audited):
    store, repo, _, task, _ = audited
    git(repo, "checkout", "-q", "--detach")

    with pytest.raises(MergeRefused, match="branch"):
        merge_preview("t1", store, repo)


def test_preview_refuses_tracked_changes_in_main(audited):
    store, repo, _, _, _ = audited
    (repo / "a.txt").write_text("edited")

    with pytest.raises(MergeRefused, match="changes"):
        merge_preview("t1", store, repo)


def test_merge_refuses_when_the_shown_sha_is_not_the_audited_sha(audited):
    store, repo, _, task, manager = audited
    main_before = git(repo, "rev-parse", "HEAD")

    with pytest.raises(MergeRefused, match="shown"):
        merge_task("t1", store, repo, manager, shown_sha="0" * 40)

    assert git(repo, "rev-parse", "HEAD") == main_before
    assert store.load("t1") == task


def test_merge_fast_forwards_main_saves_merged_and_clears_the_worktree_field(audited):
    store, repo, _, task, manager = audited

    merged = merge_task("t1", store, repo, manager, shown_sha=task.audited_sha)

    assert git(repo, "rev-parse", "HEAD") == task.audited_sha
    assert (merged.state, merged.worktree) == (TaskState.MERGED, None)
    assert store.load("t1") == merged


def test_merge_removes_the_worktree_and_keeps_the_branch(audited):
    store, repo, worktree, task, manager = audited

    merge_task("t1", store, repo, manager, shown_sha=task.audited_sha)

    assert not worktree.exists()
    assert git(repo, "branch", "--list", "factory/t1")


class FailingWorktrees:
    def remove(self, task_id):
        raise RuntimeError("cannot remove")


def test_a_failed_worktree_removal_after_the_merge_still_ends_merged(audited):
    store, repo, _, task, _ = audited

    merged = merge_task("t1", store, repo, FailingWorktrees(), shown_sha=task.audited_sha)

    assert merged.state is TaskState.MERGED
    assert store.load("t1").state is TaskState.MERGED
    assert git(repo, "rev-parse", "HEAD") == task.audited_sha


def test_a_crash_after_the_ff_is_recovered_without_merging_again(audited):
    store, repo, _, task, manager = audited
    git(repo, "merge", "--ff-only", task.audited_sha)  # the ff happened, the save did not

    merged = merge_task("t1", store, repo, manager, shown_sha=task.audited_sha)

    assert merged.state is TaskState.MERGED
    assert store.load("t1").state is TaskState.MERGED
    assert git(repo, "rev-parse", "HEAD") == task.audited_sha


def test_merge_does_not_run_hooks_from_the_repository(audited):
    store, repo, _, task, manager = audited
    hooks = repo / "hooks"
    hooks.mkdir()
    hook = hooks / "post-merge"
    hook.write_text(f"#!/bin/sh\ntouch {repo}/../hook-ran\n")
    hook.chmod(0o755)
    git(repo, "config", "core.hooksPath", str(hooks))

    merge_task("t1", store, repo, manager, shown_sha=task.audited_sha)

    assert not (repo.parent / "hook-ran").exists()


def test_merge_refuses_when_main_moved_and_leaves_everything_unchanged(audited):
    store, repo, worktree, task, manager = audited
    (repo / "m.txt").write_text("m")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "main moved")
    main_before = git(repo, "rev-parse", "HEAD")

    with pytest.raises(MergeRefused, match="moved"):
        merge_task("t1", store, repo, manager, shown_sha=task.audited_sha)

    assert git(repo, "rev-parse", "HEAD") == main_before
    assert store.load("t1") == task and worktree.exists()
