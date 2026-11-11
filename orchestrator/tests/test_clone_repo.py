from factory.clone_repo import parse_repository


def test_owner_slash_repo_becomes_the_canonical_https_url_and_the_repo_name():
    assert parse_repository("owner/repo") == ("https://github.com/owner/repo.git", "repo")
