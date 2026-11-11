import pytest

from factory.clone_repo import parse_repository


def test_owner_slash_repo_becomes_the_canonical_https_url_and_the_repo_name():
    assert parse_repository("owner/repo") == ("https://github.com/owner/repo.git", "repo")


@pytest.mark.parametrize("spec", ["https://github.com/owner/repo", "https://github.com/owner/repo.git"])
def test_a_github_https_url_with_or_without_dot_git_parses_to_the_same_url(spec):
    assert parse_repository(spec) == ("https://github.com/owner/repo.git", "repo")


@pytest.mark.parametrize("spec", ["git@github.com:owner/repo", "git@github.com:owner/repo.git"])
def test_the_github_ssh_form_keeps_ssh_in_the_canonical_url(spec):
    assert parse_repository(spec) == ("git@github.com:owner/repo.git", "repo")
