import io
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from factory.cli import IO
from factory.clone_repo import RepositoryError, clone_repository, gh_target, github_slug, parse_repository


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


class FakeGh:
    """Stands in for the runner: records each call and, like a real clone, creates the destination's parents
    (and the destination itself when it succeeds)."""

    def __init__(self, returncode=0, origin="https://github.com/o/r.git"):
        self.returncode = returncode
        self.origin = origin
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if argv[0] == "git":
            return SimpleNamespace(returncode=0 if self.origin else 1, stdout=f"{self.origin}\n")
        destination = Path(argv[4])
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.returncode == 0:
            destination.mkdir()
        return SimpleNamespace(returncode=self.returncode)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_HOME", str(tmp_path / "fh"))
    return tmp_path / "fh"


def run_clone(spec, gh):
    out, err = io.StringIO(), io.StringIO()
    return clone_repository(spec, IO(None, out, err), run=gh), out.getvalue(), err.getvalue()


def test_clone_runs_gh_into_a_project_directory_of_the_harness_home_without_an_upstream_remote(harness):
    gh = FakeGh()

    code, out, _ = run_clone("o/r", gh)

    destination = str(harness / "r" / "repo")
    assert code == 0
    assert gh.calls[0] == (["gh", "repo", "clone", "github.com/o/r", destination, "--no-upstream"],
                           {"stdin": subprocess.DEVNULL})
    assert destination in out


def test_the_success_line_names_the_origin_that_was_cloned_so_the_protocol_gh_chose_shows(harness):
    code, out, _ = run_clone("o/r", FakeGh(origin="git@github.com:o/r.git"))

    assert code == 0
    assert out == f"cloned git@github.com:o/r.git into {harness / 'r' / 'repo'}\n"


def test_the_success_line_falls_back_to_what_gh_was_given_when_the_origin_cannot_be_read(harness):
    code, out, _ = run_clone("o/r", FakeGh(origin=None))

    assert code == 0
    assert out == f"cloned github.com/o/r into {harness / 'r' / 'repo'}\n"


def test_the_global_configs_home_is_used_when_factory_home_is_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("FACTORY_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    (tmp_path / "user" / ".harness").mkdir(parents=True)
    (tmp_path / "user" / ".harness" / "factory.toml").write_text(f'home = "{tmp_path / "elsewhere"}"\n')
    gh = FakeGh()

    code, _, _ = run_clone("o/r", gh)

    assert code == 0
    assert gh.calls[0][0][4] == str(tmp_path / "elsewhere" / "r" / "repo")


def test_factory_home_wins_over_the_global_configs_home(harness, tmp_path):
    harness.mkdir()
    (harness / "factory.toml").write_text(f'home = "{tmp_path / "elsewhere"}"\n')
    gh = FakeGh()

    code, _, _ = run_clone("o/r", gh)

    assert code == 0
    assert gh.calls[0][0][4] == str(harness / "r" / "repo")
    assert not (tmp_path / "elsewhere").exists()


def test_an_existing_target_is_refused_and_left_untouched(harness):
    (harness / "r" / "repo").mkdir(parents=True)
    (harness / "r" / "repo" / "mine.txt").write_text("keep")
    gh = FakeGh()

    code, out, err = run_clone("o/r", gh)

    assert code == 1
    assert out == ""
    assert err == f"{harness / 'r' / 'repo'} already exists; not overwriting\n"
    assert [p.name for p in (harness / "r" / "repo").iterdir()] == ["mine.txt"]
    assert gh.calls == []


def test_a_failed_clone_exits_1_and_leaves_nothing_it_created(harness):
    code, _, _ = run_clone("o/missing", FakeGh(returncode=1))

    assert code == 1
    assert not harness.exists()


def test_a_failed_clone_keeps_what_was_already_in_the_project_directory(harness):
    (harness / "r" / "state").mkdir(parents=True)
    (harness / "r" / "state" / "x").write_text("keep")

    code, _, _ = run_clone("missing/r", FakeGh(returncode=1))

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
