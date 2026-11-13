import subprocess
from types import SimpleNamespace

from factory.github import GitHub


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
