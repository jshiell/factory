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


def test_a_directory_that_cannot_be_emptied_gives_worktree_not_clean_instead_of_raising(wt):
    stuck = wt / "ignored_dir"
    stuck.mkdir()
    (stuck / "f").write_text("i")
    stuck.chmod(0o555)
    try:
        assert clean_worktree(wt) == "worktree_not_clean"
    finally:
        stuck.chmod(0o755)


def test_a_rewritten_dot_git_pointing_at_a_planted_gitdir_is_refused_and_nothing_is_deleted(wt, tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "precious").write_text("p")
    git(tmp_path, "init", "-q", "--bare", str(wt / ".evil"))
    git(wt / ".evil", "config", "core.worktree", str(victim))
    (wt / ".git").write_text("gitdir: .evil\n")

    assert clean_worktree(wt) == "worktree_mismatch"

    assert (victim / "precious").read_text() == "p"


def test_a_main_working_tree_with_a_real_dot_git_directory_is_allowed(repo):
    (repo / "junk.txt").write_text("j")

    assert clean_worktree(repo) is None
    assert not (repo / "junk.txt").exists()


@pytest.mark.parametrize("inside", [".factory", ".worktrees/other", ""])
def test_factory_state_inside_the_worktree_is_refused_and_survives(wt, inside):
    state = wt / inside if inside else wt
    state.mkdir(parents=True, exist_ok=True)
    (state / "ledger.jsonl").write_text("l")

    assert clean_worktree(wt, factory_paths=(state,)) == "worktree_mismatch"

    assert (state / "ledger.jsonl").exists()


def test_factory_paths_outside_the_worktree_do_not_block_the_clean(wt, tmp_path):
    assert clean_worktree(wt, factory_paths=(tmp_path / "state", tmp_path / "other")) is None
