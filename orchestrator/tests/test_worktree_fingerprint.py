import pytest

from factory.worktree_fingerprint import worktree_fingerprint
from factory.worktrees import WorktreeGit
from gitutil import git


@pytest.fixture
def tree(repo):
    (repo / "tracked.txt").write_text("one")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-q", "-m", "tracked")
    return WorktreeGit(repo)


def edit_tracked(repo):
    (repo / "tracked.txt").write_text("two")


def stage_a_file(repo):
    (repo / "staged.txt").write_text("s")
    git(repo, "add", "staged.txt")


def add_untracked(repo):
    (repo / "untracked.txt").write_text("u")


def commit(repo):
    (repo / "committed.txt").write_text("c")
    git(repo, "add", "committed.txt")
    git(repo, "commit", "-q", "-m", "more")


def test_an_untouched_worktree_has_a_stable_fingerprint(repo, tree):
    assert worktree_fingerprint(tree) == worktree_fingerprint(tree)


@pytest.mark.parametrize("change", [edit_tracked, stage_a_file, add_untracked, commit])
def test_a_change_to_the_worktree_changes_its_fingerprint(repo, tree, change):
    before = worktree_fingerprint(tree)

    change(repo)

    assert worktree_fingerprint(tree) != before


def test_fingerprinting_leaves_the_real_index_alone(repo, tree):
    add_untracked(repo)

    worktree_fingerprint(tree)

    assert git(repo, "status", "--porcelain") == "?? untracked.txt"
