import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from factory.clone_repo import RepositoryError, github_slug


class GitHubError(RuntimeError):
    def __init__(self, stderr: str):
        super().__init__(f"gh failed: {stderr.strip()}")
        self.stderr = stderr


@dataclass(frozen=True)
class Reaction:
    content: str
    user_id: int
    login: str


@dataclass(frozen=True)
class Comment:
    id: int
    body: str
    user_id: int
    login: str


@dataclass(frozen=True)
class User:
    login: str
    id: int


class GitHub:
    """The repository's own issues through `gh`; `run` is subprocess.run-shaped and injected."""

    def __init__(self, run, owner: str, name: str):
        if github_slug(f"{owner}/{name}") != (owner, name):
            raise ValueError(f"not a plain GitHub owner and name: {owner!r}, {name!r}")
        self._run = run
        self._owner = owner
        self._name = name

    @classmethod
    def default(cls, owner: str, name: str) -> "GitHub":
        return cls(_subprocess_run, owner, name)

    def self_user(self) -> User:
        user = self._gh("api", "user")
        return User(user["login"], user["id"])

    def repo_is_private(self) -> bool:
        return self._gh("api", f"repos/{self._owner}/{self._name}")["private"]

    def comment(self, issue_number: int, body: str) -> int:
        _require_integer(issue_number)
        return self._gh("api", f"repos/{self._owner}/{self._name}/issues/{issue_number}/comments", "-f", f"body={body}")["id"]

    def comments(self, issue_number: int) -> list[Comment]:
        _require_integer(issue_number)
        found = self._gh("api", "--paginate", f"repos/{self._owner}/{self._name}/issues/{issue_number}/comments")
        return [Comment(c["id"], c["body"], c["user"]["id"], c["user"]["login"]) for c in found]

    def reactions(self, comment_id: int) -> list[Reaction]:
        _require_integer(comment_id)
        found = self._gh("api", "--paginate", f"repos/{self._owner}/{self._name}/issues/comments/{comment_id}/reactions")
        return [Reaction(r["content"], r["user"]["id"], r["user"]["login"]) for r in found]

    def _gh(self, *args: str):
        completed = self._run(["gh", *args], stdin=subprocess.DEVNULL, capture_output=True, text=True)
        if completed.returncode != 0:
            raise GitHubError(completed.stderr)
        return json.loads(completed.stdout)


_GITHUB_PREFIXES = ("https://github.com/", "git@github.com:")


def origin_slug(repo_dir: Path, run=None) -> tuple[str, str]:
    """The (owner, name) of a clone's `origin`; a local or non-GitHub origin is refused."""
    completed = (run or _subprocess_run)(
        ["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RepositoryError(f"no origin remote: {completed.stderr.strip()}")
    url = completed.stdout.strip()
    if not url.lower().startswith(_GITHUB_PREFIXES):
        raise RepositoryError(f"origin is not a GitHub repository: {url}")
    return github_slug(url)


def _subprocess_run(argv, **kwargs):
    return subprocess.run(argv, **kwargs)


def _require_integer(value) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"not an integer id: {value!r}")
