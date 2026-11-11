import io

import pytest

from factory.cli import IO
from factory.clone_repo import RepositoryError, clone_repository, parse_repository
from gitutil import git


def test_owner_slash_repo_becomes_the_canonical_https_url_and_the_repo_name():
    assert parse_repository("owner/repo") == ("https://github.com/owner/repo.git", "repo")


@pytest.mark.parametrize("spec", ["https://github.com/owner/repo", "https://github.com/owner/repo.git"])
def test_a_github_https_url_with_or_without_dot_git_parses_to_the_same_url(spec):
    assert parse_repository(spec) == ("https://github.com/owner/repo.git", "repo")


@pytest.mark.parametrize("spec", ["git@github.com:owner/repo", "git@github.com:owner/repo.git"])
def test_the_github_ssh_form_keeps_ssh_in_the_canonical_url(spec):
    assert parse_repository(spec) == ("git@github.com:owner/repo.git", "repo")


@pytest.mark.parametrize("spec", ["owner/..", "owner/.", "owner/.git", "owner/-repo", "-owner/repo", "../repo", "./repo"])
def test_names_that_could_escape_the_home_or_pose_as_options_are_refused(spec):
    with pytest.raises(RepositoryError):
        parse_repository(spec)


@pytest.mark.parametrize(
    "spec",
    [
        "https://gitlab.com/owner/repo",
        "http://github.com/owner/repo",
        "https://user:pw@github.com/owner/repo",
        "https://github.com/owner/repo/extra",
        "owner/repo/extra",
        "ssh://git@github.com/owner/repo",
        "owner/re po",
        "owner/repo;x",
        "owner/",
        "repo",
        "",
    ],
)
def test_anything_but_a_plain_github_repository_is_refused(spec):
    with pytest.raises(RepositoryError):
        parse_repository(spec)


@pytest.mark.parametrize("name", ["_shared", "_SHARED", "factory.toml", "Factory.TOML"])
def test_names_that_collide_with_the_homes_own_files_are_refused(name):
    with pytest.raises(RepositoryError):
        parse_repository(f"owner/{name}")


@pytest.fixture
def github(tmp_path, monkeypatch):
    """Redirect github.com to local bare repositories under tmp_path/remotes; no network."""
    remotes = tmp_path / "remotes"
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    for index, prefix in enumerate(("https://github.com/", "git@github.com:")):
        monkeypatch.setenv(f"GIT_CONFIG_KEY_{index}", f"url.file://{remotes}/.insteadOf")
        monkeypatch.setenv(f"GIT_CONFIG_VALUE_{index}", prefix)
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init", "-q")
    (work / "hello.txt").write_text("hi")
    git(work, "add", ".")
    git(work, "commit", "-q", "-m", "init")
    (remotes / "o").mkdir(parents=True)
    git(tmp_path, "clone", "-q", "--bare", str(work), str(remotes / "o" / "r.git"))


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "fh"))
    return tmp_path / "fh"


def run_clone(spec):
    out, err = io.StringIO(), io.StringIO()
    return clone_repository(spec, IO(None, out, err)), out.getvalue(), err.getvalue()


def test_clone_puts_the_repository_in_a_project_directory_of_the_harness_home(github, harness):
    code, out, _ = run_clone("o/r")

    assert code == 0
    assert (harness / "r" / "repo" / "hello.txt").read_text() == "hi"
    assert str(harness / "r" / "repo") in out


def test_the_global_configs_home_is_used_when_factory_home_is_unset(github, tmp_path, monkeypatch):
    monkeypatch.delenv("FACTORY_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    (tmp_path / "user" / ".harness").mkdir(parents=True)
    (tmp_path / "user" / ".harness" / "factory.toml").write_text(f'home = "{tmp_path / "elsewhere"}"\n')

    code, _, _ = run_clone("o/r")

    assert code == 0
    assert (tmp_path / "elsewhere" / "r" / "repo" / "hello.txt").exists()


def test_factory_home_wins_over_the_global_configs_home(github, harness, tmp_path):
    harness.mkdir()
    (harness / "factory.toml").write_text(f'home = "{tmp_path / "elsewhere"}"\n')

    code, _, _ = run_clone("o/r")

    assert code == 0
    assert (harness / "r" / "repo" / "hello.txt").exists()
    assert not (tmp_path / "elsewhere").exists()
