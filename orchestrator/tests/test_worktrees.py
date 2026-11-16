import subprocess

import pytest

from gitutil import git

from factory.ids import InvalidTaskId
from factory.worktrees import BranchExists, WorktreeExists, WorktreeManager, is_ancestor


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


def test_is_ancestor_is_true_for_an_ancestor_and_false_for_a_commit_that_is_not(repo):
    first = git(repo, "rev-parse", "HEAD")
    git(repo, "commit", "-q", "--allow-empty", "-m", "second")
    second = git(repo, "rev-parse", "HEAD")

    assert is_ancestor(repo, first, second) and is_ancestor(repo, second, second)
    assert not is_ancestor(repo, second, first)


def test_is_ancestor_raises_on_a_git_error_instead_of_answering_no(repo):
    with pytest.raises(subprocess.CalledProcessError):
        is_ancestor(repo, "0" * 40, "HEAD")


@pytest.mark.parametrize("task_id", ["a/b", "..", "../evil", ""])
def test_unsafe_ids_are_rejected(manager, task_id):
    with pytest.raises(InvalidTaskId):
        manager.create(task_id)
    with pytest.raises(InvalidTaskId):
        manager.remove(task_id)
