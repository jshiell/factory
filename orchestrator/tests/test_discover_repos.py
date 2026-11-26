import pytest

from factory.repos import discover_repos


def make_repo(home, name):
    (home / name / "repo").mkdir(parents=True)


def test_a_home_that_does_not_exist_has_no_repos(tmp_path):
    found = discover_repos(tmp_path / "missing")

    assert (found.names, found.problems) == ((), {})


def test_every_directory_with_a_repo_inside_is_a_repo_sorted_by_name(tmp_path):
    make_repo(tmp_path, "zeta")
    make_repo(tmp_path, "alpha.1")

    assert discover_repos(tmp_path).names == ("alpha.1", "zeta")


def test_directories_and_files_without_a_repo_are_not_repos_and_not_problems(tmp_path):
    (tmp_path / "_server").mkdir()
    (tmp_path / "_shared").mkdir()
    (tmp_path / "empty").mkdir()
    (tmp_path / "factory.toml").write_text("")

    found = discover_repos(tmp_path)

    assert (found.names, found.problems) == ((), {})


@pytest.mark.parametrize("name", ["_server", "_SERVER", "_shared", "-dash", ".hidden", "has space", "semi;colon"])
def test_a_directory_with_a_repo_but_an_unusable_name_is_a_problem_naming_the_name(tmp_path, name):
    make_repo(tmp_path, name)

    found = discover_repos(tmp_path)

    assert found.names == ()
    assert list(found.problems) == [name]
    assert "name" in found.problems[name]


def test_a_symlinked_repo_directory_is_a_problem(tmp_path):
    real = tmp_path / "elsewhere"
    real.mkdir()
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "linked").mkdir()
    (tmp_path / "home" / "linked" / "repo").symlink_to(real)

    found = discover_repos(tmp_path / "home")

    assert found.names == ()
    assert "symlink" in found.problems["linked"]


def test_a_symlinked_project_directory_is_a_problem(tmp_path):
    real = tmp_path / "elsewhere"
    (real / "repo").mkdir(parents=True)
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "linked").symlink_to(real)

    found = discover_repos(tmp_path / "home")

    assert found.names == ()
    assert "symlink" in found.problems["linked"]


def test_the_home_itself_may_be_a_symlink(tmp_path):
    make_repo(tmp_path / "real-home", "proj")
    (tmp_path / "home").symlink_to(tmp_path / "real-home")

    assert discover_repos(tmp_path / "home").names == ("proj",)


def test_a_good_repo_is_found_beside_a_bad_one(tmp_path):
    make_repo(tmp_path, "good")
    make_repo(tmp_path, ".bad")

    found = discover_repos(tmp_path)

    assert found.names == ("good",)
    assert list(found.problems) == [".bad"]
