import json
import subprocess


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
