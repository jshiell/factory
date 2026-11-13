import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from factory.clone_repo import RepositoryError
from factory.github import Comment, GitHub, GitHubError, Reaction, User, origin_slug

FIXTURES = Path(__file__).parent / "fixtures"


def test_comment_posts_the_body_raw_and_returns_the_comment_id():
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"id": 4242, "body": "@path"}')

    comment_id = GitHub(run, "o", "r").comment(7, "@path")

    assert comment_id == 4242
    argv, kwargs = calls[0]
    assert argv == ["gh", "api", "repos/o/r/issues/7/comments", "-f", "body=@path"]
    assert kwargs["stdin"] == subprocess.DEVNULL


def test_reactions_parse_the_paginated_response():
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=(FIXTURES / "gh_reactions_paginated.json").read_text())

    reactions = GitHub(run, "o", "r").reactions(4242)

    assert reactions == [
        Reaction(content="+1", user_id=1001, login="Alice"),
        Reaction(content="heart", user_id=1002, login="bob"),
        Reaction(content="-1", user_id=1001, login="Alice"),
    ]
    argv, kwargs = calls[0]
    assert argv == ["gh", "api", "--paginate", "repos/o/r/issues/comments/4242/reactions"]
    assert kwargs["stdin"] == subprocess.DEVNULL


def test_comments_parse_the_paginated_response():
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=(FIXTURES / "gh_comments_paginated.json").read_text())

    comments = GitHub(run, "o", "r").comments(7)

    assert comments == [
        Comment(id=501, body="first page comment", user_id=1001, login="Alice"),
        Comment(id=502, body="<!-- factory -->\nplan", user_id=1002, login="bob"),
        Comment(id=503, body="second page comment", user_id=1003, login="carol"),
    ]
    argv, kwargs = calls[0]
    assert argv == ["gh", "api", "--paginate", "repos/o/r/issues/7/comments"]
    assert kwargs["stdin"] == subprocess.DEVNULL


def test_self_user_returns_login_and_numeric_id():
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"login": "factory-bot", "id": 99, "type": "User"}')

    user = GitHub(run, "o", "r").self_user()

    assert user == User(login="factory-bot", id=99)
    argv, kwargs = calls[0]
    assert argv == ["gh", "api", "user"]
    assert kwargs["stdin"] == subprocess.DEVNULL


def test_repo_is_private_reads_the_private_flag():
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"name": "r", "private": true}')

    assert GitHub(run, "o", "r").repo_is_private() is True
    argv, kwargs = calls[0]
    assert argv == ["gh", "api", "repos/o/r"]
    assert kwargs["stdin"] == subprocess.DEVNULL


@pytest.mark.parametrize(
    "call",
    [
        lambda gh: gh.self_user(),
        lambda gh: gh.repo_is_private(),
        lambda gh: gh.comment(7, "hi"),
        lambda gh: gh.comments(7),
        lambda gh: gh.reactions(4242),
    ],
)
def test_a_failing_gh_raises_with_its_stderr(call):
    def run(argv, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="gh: Not Found (HTTP 404)")

    with pytest.raises(GitHubError, match="Not Found") as raised:
        call(GitHub(run, "o", "r"))

    assert raised.value.stderr == "gh: Not Found (HTTP 404)"


@pytest.mark.parametrize(
    ("owner", "name"),
    [
        ("", "r"),
        ("o", ""),
        ("-o", "r"),
        ("o", "-r"),
        ("..", "r"),
        ("o", ".."),
        ("o/x", "r"),
        ("o", "r/../x"),
        ("o", "r?x=1"),
        ("o x", "r"),
        ("https://github.com/o", "r"),
        ("o", "r.git"),
    ],
)
def test_a_bad_owner_or_name_is_refused_by_the_constructor(owner, name):
    with pytest.raises(ValueError):
        GitHub(lambda argv, **kwargs: pytest.fail("ran"), owner, name)


def test_the_default_wiring_runs_gh_through_subprocess_run(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"private": false}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert GitHub.default("o", "r").repo_is_private() is False
    assert calls == [
        (
            ["gh", "api", "repos/o/r"],
            {"stdin": subprocess.DEVNULL, "capture_output": True, "text": True},
        )
    ]


def test_origin_slug_reads_the_owner_and_name_from_the_origin_url():
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="git@github.com:Octo/Repo.git\n", stderr="")

    assert origin_slug(Path("/clone"), run) == ("Octo", "Repo")
    argv, kwargs = calls[0]
    assert argv == ["git", "-C", "/clone", "remote", "get-url", "origin"]
    assert kwargs["stdin"] == subprocess.DEVNULL


@pytest.mark.parametrize("url", ["/home/me/repo", "foo/bar", "../x", "https://gitlab.com/o/r.git", "git@example.com:o/r.git"])
def test_origin_slug_refuses_a_local_or_other_host_origin(url):
    def run(argv, **kwargs):
        return SimpleNamespace(returncode=0, stdout=url + "\n", stderr="")

    with pytest.raises(RepositoryError):
        origin_slug(Path("/clone"), run)


def test_origin_slug_refuses_a_clone_without_an_origin():
    def run(argv, **kwargs):
        return SimpleNamespace(returncode=2, stdout="", stderr="error: No such remote 'origin'")

    with pytest.raises(RepositoryError, match="No such remote"):
        origin_slug(Path("/clone"), run)


@pytest.mark.parametrize("bad_id",["7/../x", "7", 7.5, True, None])
@pytest.mark.parametrize(
    "call",
    [
        lambda gh, bad: gh.comment(bad, "hi"),
        lambda gh, bad: gh.comments(bad),
        lambda gh, bad: gh.reactions(bad),
    ],
)
def test_a_non_integer_id_is_refused_before_anything_runs(call, bad_id):
    gh = GitHub(lambda argv, **kwargs: pytest.fail("ran"), "o", "r")

    with pytest.raises(ValueError):
        call(gh, bad_id)
