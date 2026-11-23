from dataclasses import replace

import pytest

from factory.merge_task import ALREADY_IN_MAIN, MergeRefused, merge_preview, merge_task
from factory.store import TaskStore
from factory.task import InvalidTransition, Task, TaskState
from factory.worktrees import WorktreeManager
from gitutil import git
from planted_gitdir import redirect_to_planted_gitdir


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


@pytest.mark.smoke
def test_preview_of_an_audited_task_names_the_branch_diffstat_and_findings(audited):
    store, repo, _, _, _ = audited

    preview = merge_preview("t1", store, repo)

    assert preview.branch == "factory/t1"
    assert "b.txt" in preview.diffstat
    assert preview.findings == ("looks fine",)


def _audited_with(tmp_path, repo, paths):
    """An audited task t2 whose one commit adds each path."""
    (tmp_path / "tasks").mkdir(exist_ok=True)
    store = TaskStore(tmp_path / "tasks")
    worktree, base = WorktreeManager(repo, tmp_path / "wt").create("t2")
    for path in paths:
        (worktree / path).parent.mkdir(parents=True, exist_ok=True)
        (worktree / path).write_text("x")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "Task t2")
    store.save(Task(id="t2", prompt="p", state=TaskState.AUDITED, worktree=str(worktree), base_sha=base,
                    audited_sha=git(worktree, "rev-parse", "HEAD")))
    return store


def test_preview_flags_root_tool_config_the_merge_touches(tmp_path, repo):
    flagged = [".claude/settings.json", ".mcp.json", "mise.toml", ".mise.toml", ".vscode/tasks.json",
               ".github/workflows/ci.yml", ".CLAUDE/x", ".Github/Workflows/y.yml",
               ".claude/new\nline", ".claude/é"]  # names git would quote without -z
    ignored = ["b.txt", "sub/.claude/x", "sub/mise.toml", ".github/CODEOWNERS", "docs/.mcp.json"]
    store = _audited_with(tmp_path, repo, flagged + ignored)

    preview = merge_preview("t2", store, repo)

    assert sorted(preview.tool_config) == sorted(flagged)


def test_preview_flags_no_tool_config_for_an_ordinary_change(audited):
    store, repo, _, _, _ = audited

    assert merge_preview("t1", store, repo).tool_config == ()


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


def test_preview_refuses_when_main_has_moved_past_the_audited_commit(audited):
    store, repo, _, task, _ = audited
    git(repo, "merge", "-q", "--ff-only", task.audited_sha)
    git(repo, "commit", "-q", "--allow-empty", "-m", "after the merge")

    with pytest.raises(MergeRefused) as refusal:
        merge_preview("t1", store, repo)

    assert str(refusal.value) == ALREADY_IN_MAIN


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


def test_preview_refuses_another_branch_than_main_branch(audited):
    store, repo, _, _, _ = audited
    git(repo, "checkout", "-q", "-b", "other")

    with pytest.raises(MergeRefused, match="main is on branch other, not main"):
        merge_preview("t1", store, repo, main_branch="main")


def test_merge_refuses_another_branch_than_main_branch_before_any_fast_forward(audited):
    store, repo, worktree, task, manager = audited
    git(repo, "checkout", "-q", "-b", "other")
    head_before = git(repo, "rev-parse", "HEAD")

    with pytest.raises(MergeRefused, match="main is on branch other, not main"):
        merge_task("t1", store, repo, manager, shown_sha=task.audited_sha, main_branch="main")

    assert git(repo, "rev-parse", "HEAD") == head_before
    assert store.load("t1") == task and worktree.exists()


def test_crash_recovery_refuses_another_branch_than_main_branch(audited):
    store, repo, worktree, task, manager = audited
    git(repo, "checkout", "-q", "-b", "other")
    git(repo, "merge", "-q", "--ff-only", task.audited_sha)  # looks like a crashed ff, but on the wrong branch

    with pytest.raises(MergeRefused, match="main is on branch other, not main"):
        merge_task("t1", store, repo, manager, shown_sha=task.audited_sha, main_branch="main")

    assert store.load("t1") == task and worktree.exists()


def test_crash_recovery_refuses_a_detached_head_when_main_branch_is_set(audited):
    store, repo, worktree, task, manager = audited
    git(repo, "merge", "-q", "--ff-only", task.audited_sha)
    git(repo, "checkout", "-q", "--detach")

    with pytest.raises(MergeRefused, match="main is not on a branch"):
        merge_task("t1", store, repo, manager, shown_sha=task.audited_sha, main_branch="main")

    assert store.load("t1") == task and worktree.exists()


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


def test_preview_refuses_a_rewritten_dot_git_without_running_the_planted_repos_filter(audited, tmp_path):
    store, repo, worktree, _, _ = audited
    marker = redirect_to_planted_gitdir(worktree, tmp_path, clone_of=worktree)  # the clone is at the audited commit

    with pytest.raises(MergeRefused, match=r"\.git"):
        merge_preview("t1", store, repo)

    assert not marker.exists()
