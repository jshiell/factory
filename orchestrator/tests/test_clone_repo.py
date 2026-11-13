import io

import pytest

from factory.cli import IO
from factory.clone_repo import RepositoryError, clone_repository, gh_target, github_slug, parse_repository
from gitutil import git


def test_owner_slash_repo_becomes_the_canonical_https_url_and_the_repo_name():
    assert parse_repository("owner/repo") == ("https://github.com/owner/repo.git", "repo")


@pytest.mark.parametrize("spec", ["https://github.com/owner/repo", "https://github.com/owner/repo.git"])
def test_a_github_https_url_with_or_without_dot_git_parses_to_the_same_url(spec):
    assert parse_repository(spec) == ("https://github.com/owner/repo.git", "repo")


@pytest.mark.parametrize("spec", ["git@github.com:owner/repo", "git@github.com:owner/repo.git"])
def test_the_github_ssh_form_keeps_ssh_in_the_canonical_url(spec):
    assert parse_repository(spec) == ("git@github.com:owner/repo.git", "repo")


@pytest.mark.parametrize(
    "spec",
    [
        "Owner/Repo",
        "https://github.com/Owner/Repo",
        "HTTPS://GITHUB.COM/Owner/Repo.git",
        "git@github.com:Owner/Repo",
        "git@github.com:Owner/Repo.git",
    ],
)
def test_github_slug_is_the_owner_and_name_of_every_accepted_form_with_case_kept(spec):
    assert github_slug(spec) == ("Owner", "Repo")


@pytest.mark.parametrize("spec", ["owner/..","owner/.", "owner/.git", "owner/-repo", "-owner/repo", "../repo", "./repo"])
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


def test_an_existing_target_is_refused_and_left_untouched(github, harness):
    (harness / "r" / "repo").mkdir(parents=True)
    (harness / "r" / "repo" / "mine.txt").write_text("keep")

    code, out, err = run_clone("o/r")

    assert code == 1
    assert out == ""
    assert err == f"{harness / 'r' / 'repo'} already exists; not overwriting\n"
    assert [p.name for p in (harness / "r" / "repo").iterdir()] == ["mine.txt"]


def test_a_failed_clone_exits_1_and_leaves_nothing_it_created(github, harness):
    code, _, _ = run_clone("o/missing")

    assert code == 1
    assert not harness.exists()


def test_a_failed_clone_keeps_what_was_already_in_the_project_directory(github, harness):
    (harness / "r" / "state").mkdir(parents=True)
    (harness / "r" / "state" / "x").write_text("keep")

    code, _, _ = run_clone("missing/r")

    assert code == 1
    assert not (harness / "r" / "repo").exists()
    assert (harness / "r" / "state" / "x").read_text() == "keep"


@pytest.mark.parametrize(
    ("spec", "url"),
    [("HTTPS://GitHub.com/o/R", "https://github.com/o/R.git"), ("GIT@GITHUB.COM:o/R.git", "git@github.com:o/R.git")],
)
def test_the_host_is_matched_case_insensitively_and_the_owner_and_name_keep_their_case(spec, url):
    assert parse_repository(spec) == (url, "R")


def test_a_bare_slug_is_handed_to_gh_with_the_host_pinned_so_gh_picks_the_protocol():
    assert gh_target("o/r") == "github.com/o/r"


@pytest.mark.parametrize(
    ("spec", "target"),
    [("HTTPS://GitHub.com/o/R", "https://github.com/o/R.git"), ("git@github.com:o/r", "git@github.com:o/r.git")],
)
def test_a_spec_typed_with_a_prefix_keeps_its_protocol_as_the_canonical_url(spec, target):
    assert gh_target(spec) == target


def test_a_bare_slugs_dot_git_is_dropped_and_its_case_kept():
    assert gh_target("Owner/Repo.git") == "github.com/Owner/Repo"
