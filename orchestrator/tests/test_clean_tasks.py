import shutil
import subprocess

import pytest

from gitutil import git

from factory.clean_tasks import clean_candidates, clean_tasks
from factory.store import TaskStore
from factory.task import Task, TaskState
from factory.worktrees import WorktreeManager

S = TaskState


@pytest.fixture
def env(tmp_path, repo):
    (tmp_path / "tasks").mkdir()
    store = TaskStore(tmp_path / "tasks")
    manager = WorktreeManager(repo, tmp_path / "wt")

    def add(task_id, state, with_worktree=True):
        if with_worktree:
            path, base = manager.create(task_id)
            store.save(Task(id=task_id, prompt="", state=state, worktree=str(path), base_sha=base))
            return path
        store.save(Task(id=task_id, prompt="", state=state))

    return store, repo, manager, tmp_path / "wt", add


def ids(candidates):
    return sorted(c.task.id for c in candidates)


def test_candidates_are_the_merged_and_failed_tasks_with_a_worktree(env):
    store, repo, _, root, add = env
    add("failed", S.FAILED)
    add("merged", S.MERGED)

    assert ids(clean_candidates(store, repo, root)) == ["failed", "merged"]


@pytest.mark.parametrize("state", [s for s in S if s not in (S.FAILED, S.MERGED)])
def test_a_task_that_is_not_merged_or_failed_is_never_a_candidate(env, state):
    store, repo, _, root, add = env
    add("t1", state)

    assert clean_candidates(store, repo, root) == []


def test_a_merged_task_without_a_worktree_but_with_a_live_branch_is_a_candidate(env):
    store, repo, manager, root, add = env
    path = add("merged", S.MERGED)
    manager.remove("merged")
    store.save(Task(id="merged", prompt="", state=S.MERGED))  # worktree cleared, as after a merge
    add("bare", S.MERGED, with_worktree=False)  # no worktree and no branch

    assert ids(clean_candidates(store, repo, root)) == ["merged"]


def candidate(env, task_id):
    store, repo, _, root, _ = env
    return next(c for c in clean_candidates(store, repo, root) if c.task.id == task_id)


def test_the_flags_show_a_missing_directory_dirty_state_and_commits_ahead_of_base(env):
    _, _, _, _, add = env
    clean_path = add("clean", S.FAILED)
    dirty_path = add("dirty", S.FAILED)
    (dirty_path / "x.txt").write_text("x")
    git(dirty_path, "add", "-A")
    git(dirty_path, "commit", "-q", "-m", "work")
    (dirty_path / "y.txt").write_text("y")
    gone_path = add("gone", S.FAILED)
    git(gone_path.parent.parent / "repo", "worktree", "remove", "--force", str(gone_path))

    clean, dirty, gone = (candidate(env, name) for name in ("clean", "dirty", "gone"))

    assert (clean.directory_exists, clean.dirty, clean.commits_ahead) == (True, False, 0)
    assert (dirty.directory_exists, dirty.dirty, dirty.commits_ahead) == (True, True, 1)
    assert (gone.directory_exists, gone.dirty, gone.commits_ahead) == (False, False, 0)


def test_a_worktree_outside_the_worktree_root_is_flagged_not_cleaned(env, tmp_path):
    store, repo, _, root, add = env
    add("t1", S.FAILED)
    store.save(Task(id="t1", prompt="", state=S.FAILED, worktree=str(tmp_path / "elsewhere"), base_sha="x"))

    assert candidate(env, "t1").outside_root is True
    assert candidate(env, "t1").directory_exists is False


def run_clean(env):
    store, repo, manager, root, _ = env
    return clean_tasks(clean_candidates(store, repo, root), store, manager, repo, root)


def test_clean_removes_the_worktree_and_clears_the_field(env):
    store, _, _, _, add = env
    path = add("failed", S.FAILED)

    run_clean(env)

    assert not path.exists() and store.load("failed").worktree is None


def test_a_missing_directory_is_pruned_and_the_merged_branch_then_deletes(env):
    store, repo, _, _, add = env
    path = add("merged", S.MERGED)
    shutil.rmtree(path)  # the directory is gone but git still has the worktree registered

    outcomes = run_clean(env)

    assert store.load("merged").worktree is None
    assert not git(repo, "branch", "--list", "factory/merged")
    assert outcomes[0].problems == []


def test_a_merged_branch_is_deleted_and_a_failed_branch_is_kept(env):
    store, repo, _, _, add = env
    add("merged", S.MERGED)
    add("failed", S.FAILED)

    run_clean(env)

    assert not git(repo, "branch", "--list", "factory/merged")
    assert git(repo, "branch", "--list", "factory/failed")


def test_a_refused_branch_delete_is_reported_and_the_branch_kept(env):
    store, repo, _, _, add = env
    path = add("merged", S.MERGED)
    (path / "x.txt").write_text("x")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "unmerged work")  # inconsistent: merged task with an unmerged branch

    outcomes = run_clean(env)

    assert git(repo, "branch", "--list", "factory/merged")
    assert any("branch" in problem for problem in outcomes[0].problems)


def test_branches_are_not_deleted_while_main_is_not_on_a_branch(env):
    store, repo, _, _, add = env
    add("merged", S.MERGED)
    git(repo, "checkout", "-q", "--detach")

    outcomes = run_clean(env)

    assert git(repo, "branch", "--list", "factory/merged")
    assert any("main is not on a branch" in problem for problem in outcomes[0].problems)


class FailingFirst:
    """Fails to remove the first task's worktree, delegates the rest."""

    def __init__(self, manager, failing_id):
        self._manager, self._failing_id = manager, failing_id

    def remove(self, task_id):
        if task_id == self._failing_id:
            raise subprocess.CalledProcessError(1, ["git", "worktree", "remove"])
        self._manager.remove(task_id)


def test_a_removal_failure_is_reported_keeps_that_task_and_the_others_are_still_cleaned(env):
    store, repo, manager, root, add = env
    bad = add("bad", S.MERGED)
    good = add("good", S.FAILED)

    outcomes = clean_tasks(clean_candidates(store, repo, root), store, FailingFirst(manager, "bad"), repo, root)

    by_id = {o.task_id: o for o in outcomes}
    assert by_id["bad"].problems and not by_id["good"].problems
    assert store.load("bad").worktree == str(bad) and git(repo, "branch", "--list", "factory/bad")
    assert not good.exists() and store.load("good").worktree is None


def test_a_task_that_is_no_longer_terminal_at_execution_time_is_skipped(env):
    store, repo, manager, root, add = env
    path = add("t1", S.FAILED)
    candidates = clean_candidates(store, repo, root)
    store.save(Task(id="t1", prompt="", state=S.QUEUED, worktree=str(path), base_sha="x"))

    outcomes = clean_tasks(candidates, store, manager, repo, root)

    assert path.exists() and outcomes[0].problems


def test_a_worktree_outside_the_root_is_reported_and_left_alone(env, tmp_path):
    store, repo, manager, root, add = env
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    store.save(Task(id="t1", prompt="", state=S.FAILED, worktree=str(elsewhere), base_sha="x"))

    outcomes = run_clean(env)

    assert elsewhere.exists() and store.load("t1").worktree == str(elsewhere)
    assert any("outside" in problem for problem in outcomes[0].problems)


def test_a_worktree_under_the_root_but_not_at_root_slash_id_is_flagged(env):
    store, repo, manager, root, add = env
    other = add("other", S.QUEUED)
    store.save(Task(id="t1", prompt="", state=S.FAILED, worktree=str(other), base_sha="x"))  # points at another task's

    assert candidate(env, "t1").outside_root is True
    outcomes = run_clean(env)

    assert other.exists()
    assert any(o.task_id == "t1" and o.problems for o in outcomes)
