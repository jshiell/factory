import pytest

from factory.repos import repo_name_from_cwd


@pytest.fixture
def home(tmp_path):
    home = tmp_path / "home"
    for sub in ("proj/repo/src/deep", "proj/worktrees/t1/src", "proj/state", "_server", "_shared"):
        (home / sub).mkdir(parents=True)
    return home


@pytest.mark.parametrize("cwd", ["proj", "proj/repo", "proj/repo/src/deep", "proj/worktrees/t1", "proj/worktrees/t1/src"])
def test_a_directory_of_a_repo_project_names_that_repo(home, cwd):
    assert repo_name_from_cwd(home, home / cwd) == "proj"


@pytest.mark.parametrize("cwd", [".", "proj/state", "proj/worktrees", "_server", "_shared"])
def test_a_directory_of_the_home_that_is_not_a_repo_names_none(home, cwd):
    assert repo_name_from_cwd(home, home / cwd) is None


def test_a_directory_outside_the_home_names_none(home, tmp_path):
    assert repo_name_from_cwd(home, tmp_path) is None
    assert repo_name_from_cwd(home, tmp_path / "home-other") is None


def test_paths_are_compared_resolved_so_a_symlinked_home_or_cwd_still_matches(home, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(home)

    assert repo_name_from_cwd(link, home / "proj/repo") == "proj"
    assert repo_name_from_cwd(home, link / "proj/repo/src") == "proj"
