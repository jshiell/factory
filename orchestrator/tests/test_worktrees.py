import pytest

from gitutil import git

from factory.ids import InvalidTaskId
from factory.worktrees import BranchExists, WorktreeExists, WorktreeManager


@pytest.fixture
def manager(repo, tmp_path):
    return WorktreeManager(repo, tmp_path / "worktrees")


def test_create_makes_a_branch_worktree_at_the_current_head(repo, manager):
    path, base_sha = manager.create("t1")

    assert base_sha == git(repo, "rev-parse", "HEAD")
    assert git(path, "rev-parse", "HEAD") == base_sha
    assert git(path, "rev-parse", "--abbrev-ref", "HEAD") == "factory/t1"


def test_creating_twice_raises_worktree_exists(manager):
    manager.create("t1")

    with pytest.raises(WorktreeExists):
        manager.create("t1")


def test_existing_branch_without_a_worktree_raises_branch_exists(repo, manager):
    git(repo, "branch", "factory/t1")

    with pytest.raises(BranchExists):
        manager.create("t1")


def test_remove_deletes_a_dirty_worktree_but_keeps_the_branch(repo, manager):
    path, _sha = manager.create("t1")
    (path / "untracked.txt").write_text("junk")
    (path / "a.txt").write_text("modified")

    manager.remove("t1")

    assert not path.exists()
    assert git(repo, "branch", "--list", "factory/t1") != ""


@pytest.mark.parametrize("task_id", ["a/b", "..", "../evil", ""])
def test_unsafe_ids_are_rejected(manager, task_id):
    with pytest.raises(InvalidTaskId):
        manager.create(task_id)
    with pytest.raises(InvalidTaskId):
        manager.remove(task_id)
