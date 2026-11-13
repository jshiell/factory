import json
import subprocess
from dataclasses import dataclass


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


class GitHub:
    """The repository's own issues through `gh`; `run` is subprocess.run-shaped and injected."""

    def __init__(self, run, owner: str, name: str):
        self._run = run
        self._owner = owner
        self._name = name

    def comment(self, issue_number: int, body: str) -> int:
        argv = ["gh", "api", f"repos/{self._owner}/{self._name}/issues/{issue_number}/comments", "-f", f"body={body}"]
        completed = self._run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True)
        return json.loads(completed.stdout)["id"]

    def comments(self, issue_number: int) -> list[Comment]:
        argv = ["gh", "api", "--paginate", f"repos/{self._owner}/{self._name}/issues/{issue_number}/comments"]
        completed = self._run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True)
        return [Comment(c["id"], c["body"], c["user"]["id"], c["user"]["login"]) for c in json.loads(completed.stdout)]

    def reactions(self, comment_id: int) -> list[Reaction]:
        argv = ["gh", "api", "--paginate", f"repos/{self._owner}/{self._name}/issues/comments/{comment_id}/reactions"]
        completed = self._run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True)
        return [Reaction(r["content"], r["user"]["id"], r["user"]["login"]) for r in json.loads(completed.stdout)]
