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


def break_dirty(store, repo, worktree, task):
    (worktree / "b.txt").write_text("changed")


def break_worktree_head(store, repo, worktree, task):
    (worktree / "c.txt").write_text("c")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "extra")


def break_detached(store, repo, worktree, task):
    git(worktree, "checkout", "-q", "--detach")


def break_main_not_moved(store, repo, worktree, task):
    git(repo, "reset", "-q", "--hard", task.base_sha)


def break_already_in_main(store, repo, worktree, task):
    git(repo, "merge", "-q", "--no-edit", task.audited_sha)


def break_fix_in_flight(store, repo, worktree, task):
    store.save(replace(task, fix_in_flight=True))


def break_no_worktree(store, repo, worktree, task):
    store.save(replace(task, worktree=None))


def break_worktree_dir_missing(store, repo, worktree, task):
    store.save(replace(task, worktree=str(worktree) + "-gone"))


REFUSALS = [
    (break_dirty, "dirty"), (break_worktree_head, "audited commit"), (break_detached, "factory/t1"),
    (break_main_not_moved, "has not moved"), (break_already_in_main, "already in main"),
    (break_fix_in_flight, "advance"), (break_no_worktree, "worktree"), (break_worktree_dir_missing, "worktree"),
]


@pytest.mark.parametrize("break_it, message", REFUSALS, ids=[fn.__name__ for fn, _ in REFUSALS])
def test_a_failed_precondition_refuses_and_changes_nothing(audited, break_it, message):
    store, repo, worktree, task = audited
    break_it(store, repo, worktree, task)
    stored_before = store.load("t1")
    heads_before = (git(worktree, "rev-parse", "HEAD"), git(repo, "rev-parse", "factory/t1"), git(repo, "rev-parse", "HEAD"))

    with pytest.raises(RebaseRefused, match=message):
        rebase_task("t1", store, repo)

    assert store.load("t1") == stored_before
    assert (git(worktree, "rev-parse", "HEAD"), git(repo, "rev-parse", "factory/t1"), git(repo, "rev-parse", "HEAD")) == heads_before


def test_a_task_that_is_not_audited_is_refused_by_the_state_machine(audited):
    from factory.task import InvalidTransition
    store, repo, _, task = audited
    store.save(replace(task, state=TaskState.VERIFIED))

    with pytest.raises(InvalidTransition):
        rebase_task("t1", store, repo)


def conflict_on_main(repo):
    (repo / "b.txt").write_text("main's b")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "main also adds b")


def in_progress(worktree):
    return any((worktree / git(worktree, "rev-parse", "--git-path", name)).exists() for name in ("rebase-merge", "rebase-apply"))


def test_a_conflict_is_refused_the_rebase_aborted_and_the_old_state_left_intact(audited):
    store, repo, worktree, task = audited
    conflict_on_main(repo)
    stored_before = store.load("t1")

    with pytest.raises(RebaseRefused, match="conflict.*abandon.*retry"):
        rebase_task("t1", store, repo)

    assert store.load("t1") == stored_before
    assert git(worktree, "rev-parse", "HEAD") == git(repo, "rev-parse", "factory/t1") == task.audited_sha
    assert git(worktree, "status", "--porcelain") == "" and not in_progress(worktree)
