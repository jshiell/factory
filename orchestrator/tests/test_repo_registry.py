import pytest

from factory.repos import RepoRegistry, UnknownRepo


def test_a_started_repo_is_found_by_name():
    registry = RepoRegistry({"proj": "context"}, {})

    assert registry.get("proj") == "context"
    assert registry.started_names == ("proj",)


def test_started_repos_are_listed_sorted():
    assert RepoRegistry({"b": 1, "a": 2}, {}).started_names == ("a", "b")


def test_a_skipped_repo_is_not_found_and_says_why():
    registry = RepoRegistry({}, {"busy": "locked by another factory"})

    with pytest.raises(UnknownRepo, match=r"busy.*locked by another factory"):
        registry.get("busy")
    assert registry.skipped == {"busy": "locked by another factory"}


def test_an_unknown_name_raises_naming_the_known_repos():
    with pytest.raises(UnknownRepo, match=r"nope.*known: a, b"):
        RepoRegistry({"a": 1, "b": 2}, {}).get("nope")


def test_an_empty_registry_is_valid():
    registry = RepoRegistry({}, {})

    assert registry.started_names == ()
    with pytest.raises(UnknownRepo, match="no repos"):
        registry.get("x")
