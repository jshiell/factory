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


def test_a_merged_branch_is_deleted_and_an_empty_failed_branch_is_deleted_too(env):
    store, repo, _, _, add = env
    add("merged", S.MERGED)
    add("failed", S.FAILED)

    run_clean(env)

    assert not git(repo, "branch", "--list", "factory/merged")
    assert not git(repo, "branch", "--list", "factory/failed")


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


# --- gate caches ---------------------------------------------------------------

import os
import stat as stat_module

from factory.clean_tasks import Outcome


def make_cache(tmp_path, task_id, content="abc"):
    directory = tmp_path / "gate-cache" / task_id
    directory.mkdir(parents=True)
    (directory / "f").write_text(content)
    return directory


def test_a_terminal_task_with_a_cache_reports_its_size_and_a_task_with_only_a_cache_is_a_candidate(env, tmp_path):
    store, repo, _, root, add = env
    add("failed", S.FAILED)
    add("bare", S.MERGED, with_worktree=False)
    make_cache(tmp_path, "failed")
    make_cache(tmp_path, "bare")

    found = {c.task.id: c for c in clean_candidates(store, repo, root, tmp_path / "gate-cache")}

    assert set(found) == {"failed", "bare"} and all(c.cache_bytes > 0 for c in found.values())


def test_the_caches_of_non_terminal_tasks_are_never_candidates(env, tmp_path):
    store, repo, _, root, add = env
    for state in (S.RUNNING, S.AUDITED, S.PROPOSED):
        add(f"t-{state.value}", state, with_worktree=False)
        make_cache(tmp_path, f"t-{state.value}")

    assert clean_candidates(store, repo, root, tmp_path / "gate-cache") == []


def test_without_a_cache_root_a_cache_changes_nothing(env, tmp_path):
    store, repo, _, root, add = env
    add("bare", S.MERGED, with_worktree=False)
    make_cache(tmp_path, "bare")

    assert clean_candidates(store, repo, root) == []


def run_clean_with_caches(env, tmp_path, task_id="failed"):
    store, repo, manager, root, _ = env
    candidates = clean_candidates(store, repo, root, tmp_path / "gate-cache")
    return clean_tasks(candidates, store, manager, repo, root, tmp_path / "gate-cache")


def test_clean_removes_a_terminal_tasks_cache_directory(env, tmp_path):
    _, _, _, _, add = env
    add("failed", S.FAILED)
    cache = make_cache(tmp_path, "failed")

    outcomes = run_clean_with_caches(env, tmp_path)

    assert not cache.exists() and not any(o.problems for o in outcomes)


def test_a_symlink_inside_a_cache_is_removed_without_touching_its_target(env, tmp_path):
    _, _, _, _, add = env
    add("failed", S.FAILED)
    cache = make_cache(tmp_path, "failed")
    target = tmp_path / "precious"
    target.mkdir()
    (target / "keep").write_text("keep")
    os.symlink(target, cache / "link")

    run_clean_with_caches(env, tmp_path)

    assert not cache.exists() and (target / "keep").read_text() == "keep"


def test_a_removal_failure_is_reported_as_partial_and_the_other_tasks_are_still_cleaned(env, tmp_path):
    _, _, _, _, add = env
    add("a-failed", S.FAILED)
    add("b-failed", S.FAILED)
    stuck = make_cache(tmp_path, "a-failed")
    (stuck / "sub").mkdir()
    (stuck / "sub" / "g").write_text("g")
    (stuck / "sub").chmod(0o500)  # its entries cannot be unlinked
    other = make_cache(tmp_path, "b-failed")
    try:
        outcomes = run_clean_with_caches(env, tmp_path)
    finally:
        (stuck / "sub").chmod(0o700)

    problems = [p for o in outcomes for p in o.problems]
    assert any("partially removed" in p for p in problems) and not other.exists()


def test_a_task_that_is_no_longer_terminal_keeps_its_cache(env, tmp_path):
    store, repo, manager, root, add = env
    add("failed", S.FAILED)
    cache = make_cache(tmp_path, "failed")
    candidates = clean_candidates(store, repo, root, tmp_path / "gate-cache")
    store.save(Task(id="failed", prompt="", state=S.QUEUED))

    clean_tasks(candidates, store, manager, repo, root, tmp_path / "gate-cache")

    assert cache.exists()


def test_a_worktree_failure_does_not_stop_the_cache_removal(env, tmp_path, monkeypatch):
    store, repo, manager, root, add = env
    add("failed", S.FAILED)
    cache = make_cache(tmp_path, "failed")
    candidates = clean_candidates(store, repo, root, tmp_path / "gate-cache")
    monkeypatch.setattr(manager, "remove", lambda task_id: (_ for _ in ()).throw(subprocess.CalledProcessError(1, "git")))

    outcomes = clean_tasks(candidates, store, manager, repo, root, tmp_path / "gate-cache")

    assert not cache.exists() and any("worktree kept" in p for o in outcomes for p in o.problems)


def test_an_outside_root_worktree_does_not_stop_the_cache_removal(env, tmp_path):
    store, repo, manager, root, add = env
    add("failed", S.FAILED)
    store.save(Task(id="failed", prompt="", state=S.FAILED, worktree=str(tmp_path / "elsewhere")))
    cache = make_cache(tmp_path, "failed")

    outcomes = run_clean_with_caches(env, tmp_path)

    assert not cache.exists() and any("outside the worktree root" in p for o in outcomes for p in o.problems)


from factory.clean_tasks import remove_orphan_caches
from factory.gate_cache import OrphanCache, orphan_caches


def test_orphan_caches_are_removed(env, tmp_path):
    store = env[0]
    gone = make_cache(tmp_path, "gone")

    outcomes = remove_orphan_caches(store, tmp_path / "gate-cache", orphan_caches(store, tmp_path / "gate-cache").orphans)

    assert not gone.exists() and outcomes[0].done == ["removed orphan gate cache"]


def test_an_orphan_that_gained_a_task_record_is_kept(env, tmp_path):
    store, _, _, _, add = env
    kept = make_cache(tmp_path, "late")
    orphans = orphan_caches(store, tmp_path / "gate-cache").orphans
    add("late", S.FAILED, with_worktree=False)

    outcomes = remove_orphan_caches(store, tmp_path / "gate-cache", orphans)

    assert kept.exists() and outcomes[0].problems


def test_an_orphan_swapped_for_a_symlink_is_not_followed(env, tmp_path):
    store = env[0]
    make_cache(tmp_path, "gone")
    orphans = orphan_caches(store, tmp_path / "gate-cache").orphans
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "keep").write_text("k")
    shutil.rmtree(tmp_path / "gate-cache" / "gone")
    os.symlink(precious, tmp_path / "gate-cache" / "gone")

    outcomes = remove_orphan_caches(store, tmp_path / "gate-cache", orphans)

    assert (precious / "keep").exists() and outcomes[0].problems


def commit_on(path, name="x.txt"):
    (path / name).write_text("x")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "work")


def test_a_branch_still_at_the_base_is_empty(env):
    add = env[4]
    add("failed", S.FAILED)

    assert candidate(env, "failed").branch_empty is True


def test_a_branch_with_a_commit_is_not_empty(env):
    add = env[4]
    commit_on(add("failed", S.FAILED))

    assert candidate(env, "failed").branch_empty is False


def test_an_unset_base_or_a_missing_branch_is_never_empty(env):
    store, repo, manager, root, add = env
    path = add("nobase", S.FAILED)
    store.save(Task(id="nobase", prompt="", state=S.FAILED, worktree=str(path)))  # base_sha unset
    path = add("nobranch", S.FAILED)
    git(repo, "worktree", "remove", "--force", str(path))
    git(repo, "branch", "-D", "factory/nobranch")
    store.save(Task(id="nobranch", prompt="", state=S.FAILED, worktree=str(path), base_sha="deadbeef"))

    assert candidate(env, "nobase").branch_empty is False
    assert [c for c in clean_candidates(store, repo, root) if c.task.id == "nobranch"][0].branch_empty is False


def clear_worktree(env, task_id):
    store, repo, manager, _, add = env
    task = store.load(task_id)
    manager.remove(task_id)
    store.save(Task(**{**task.__dict__, "worktree": None}))


def test_a_failed_task_without_a_worktree_is_a_candidate_only_for_an_empty_branch(env):
    store, repo, manager, root, add = env
    add("empty", S.FAILED)
    clear_worktree(env, "empty")
    commit_on(add("work", S.FAILED))
    clear_worktree(env, "work")
    add("bare", S.FAILED, with_worktree=False)  # nothing left at all

    assert ids(clean_candidates(store, repo, root)) == ["empty"]


def branch_exists(repo, task_id):
    return bool(git(repo, "branch", "--list", f"factory/{task_id}"))


def test_clean_deletes_an_empty_failed_branch_after_removing_its_worktree(env):
    store, repo, _, _, add = env
    path = add("failed", S.FAILED)

    outcomes = run_clean(env)

    assert not path.exists() and not branch_exists(repo, "failed")
    assert "deleted branch" in outcomes[0].done and outcomes[0].problems == []


def test_clean_keeps_a_failed_branch_that_holds_a_commit(env):
    store, repo, _, _, add = env
    commit_on(add("failed", S.FAILED))

    outcomes = run_clean(env)

    assert branch_exists(repo, "failed") and outcomes[0].problems == []


def test_an_empty_failed_branch_is_deleted_even_when_the_base_is_no_longer_an_ancestor_of_main(env):
    store, repo, _, _, add = env
    add("failed", S.FAILED)
    rewritten = git(repo, "commit-tree", "-m", "rewritten main", git(repo, "hash-object", "-t", "tree", "/dev/null"))
    git(repo, "reset", "-q", "--hard", rewritten)  # `branch -d` refuses this; the branch holds nothing

    outcomes = run_clean(env)

    assert not branch_exists(repo, "failed")
    assert "deleted branch" in outcomes[0].done and outcomes[0].problems == []


def test_an_empty_failed_branch_is_deleted_while_main_is_not_on_a_branch(env):
    store, repo, _, _, add = env
    add("failed", S.FAILED)
    git(repo, "checkout", "-q", "--detach")

    outcomes = run_clean(env)

    assert not branch_exists(repo, "failed")
    assert "deleted branch" in outcomes[0].done and outcomes[0].problems == []


def test_a_commit_made_between_the_preview_and_the_clean_keeps_the_branch(env):
    store, repo, manager, root, add = env
    path = add("failed", S.FAILED)
    candidates = clean_candidates(store, repo, root)
    assert candidates[0].branch_empty
    commit_on(path)

    outcomes = clean_tasks(candidates, store, manager, repo, root)

    assert branch_exists(repo, "failed") and outcomes[0].problems == []


def test_a_branch_deleted_between_the_preview_and_the_clean_is_skipped_quietly(env):
    store, repo, manager, root, add = env
    add("failed", S.FAILED)
    clear_worktree(env, "failed")
    candidates = clean_candidates(store, repo, root)
    git(repo, "branch", "-D", "factory/failed")

    outcomes = clean_tasks(candidates, store, manager, repo, root)

    assert outcomes[0].problems == []


def test_a_registered_worktree_with_the_task_field_cleared_makes_git_refuse_and_is_reported(env):
    store, repo, manager, root, add = env
    add("failed", S.FAILED)
    task = store.load("failed")
    store.save(Task(**{**task.__dict__, "worktree": None}))  # git still has the worktree registered

    outcomes = run_clean(env)

    assert branch_exists(repo, "failed")
    assert any("factory/failed" in problem for problem in outcomes[0].problems)


def test_a_refusal_for_a_registered_worktree_quotes_git_and_does_not_claim_unmerged(env):
    store, repo, manager, root, add = env
    add("failed", S.FAILED)
    task = store.load("failed")
    store.save(Task(**{**task.__dict__, "worktree": None}))

    problem = next(p for p in run_clean(env)[0].problems if "factory/failed" in p)

    assert "worktree" in problem and "not merged into main" not in problem


def test_a_refusal_for_an_unmerged_branch_quotes_git(env):
    store, repo, _, _, add = env
    commit_on(add("merged", S.MERGED))  # a merged task whose branch still holds a commit main lacks: `branch -d` refuses

    problem = next(p for p in run_clean(env)[0].problems if "factory/merged" in p)

    assert "not fully merged" in problem
