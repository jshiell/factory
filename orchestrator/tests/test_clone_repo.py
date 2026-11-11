import pytest

from factory.clone_repo import RepositoryError, parse_repository


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
