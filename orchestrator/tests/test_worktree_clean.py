import os

import pytest

from gitutil import git

from factory.worktree_clean import clean_worktree


@pytest.fixture
def wt(repo, tmp_path):
    """A linked worktree like the factory makes, with one tracked project file."""
    (repo / "proj").mkdir()
    (repo / "proj" / "code.py").write_text("x")
    (repo / ".gitignore").write_text("ignored.txt\nignored_dir/\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "project")
    path = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "factory/t1", str(path))
    return path


def test_it_removes_untracked_and_ignored_files_and_directories_and_keeps_tracked_ones(wt):
    (wt / "proj" / "untracked.py").write_text("u")
    (wt / "ignored.txt").write_text("i")
    (wt / "ignored_dir").mkdir()
    (wt / "ignored_dir" / "f").write_text("i")
    (wt / "proj" / "code.py").write_text("edited but tracked")

    assert clean_worktree(wt) is None

    assert not (wt / "proj" / "untracked.py").exists()
    assert not (wt / "ignored.txt").exists() and not (wt / "ignored_dir").exists()
    assert (wt / "proj" / "code.py").read_text() == "edited but tracked"
    assert (wt / ".git").is_file()


def test_it_removes_a_file_ignored_only_through_the_repository_exclude_file(repo, wt):
    exclude = repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(exist_ok=True)
    exclude.write_text("secret_conftest.py\n")
    (wt / "proj" / "secret_conftest.py").write_text("planted")

    clean_worktree(wt)

    assert not (wt / "proj" / "secret_conftest.py").exists()


def test_it_removes_a_venv_that_ignores_itself_with_a_star_gitignore(wt):
    site = wt / "proj" / ".venv" / "lib" / "site-packages"
    site.mkdir(parents=True)
    (wt / "proj" / ".venv" / ".gitignore").write_text("*\n")
    (site / "x.pth").write_text("import os")

    clean_worktree(wt)

    assert not (wt / "proj" / ".venv").exists()


def test_it_removes_a_symlink_without_touching_its_target(wt, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    os.symlink(outside, wt / "proj" / "link")

    clean_worktree(wt)

    assert not (wt / "proj" / "link").exists() and not (wt / "proj" / "link").is_symlink()
    assert (outside / "keep").read_text() == "keep"
