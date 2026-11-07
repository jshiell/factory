import pytest

from gitutil import git

from factory.clean_tasks import clean_candidates
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
