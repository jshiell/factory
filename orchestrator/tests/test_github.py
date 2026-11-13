import subprocess
from pathlib import Path
from types import SimpleNamespace

from factory.github import Comment, GitHub, Reaction

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
