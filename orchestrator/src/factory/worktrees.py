import subprocess
from pathlib import Path

from factory.ids import require_safe_id


# The worktree is agent-controlled: never let its config run commands (fsmonitor, hooks) in the factory's git.
HARDENED_GIT = ("git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "diff.ignoreSubmodules=all")


def run_git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        [*HARDENED_GIT, *args], cwd=cwd, capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL, env=env
    )
    return result.stdout.strip()


class WorktreeExists(Exception):
    pass


class BranchExists(Exception):
    pass


class WorktreeManager:
    def __init__(self, repo: Path, root: Path):
        self._repo = repo
        self._root = root

    def create(self, task_id: str) -> tuple[Path, str]:
        require_safe_id(task_id)
        path = self._root / task_id
        branch = f"factory/{task_id}"
        if path.exists():
            raise WorktreeExists(task_id)
        if run_git(self._repo, "branch", "--list", branch):
            raise BranchExists(branch)
        base_sha = run_git(self._repo, "rev-parse", "HEAD")
        run_git(self._repo, "worktree", "add", "-q", "-b", branch, str(path), base_sha)
        return path, base_sha

    def remove(self, task_id: str) -> None:
        require_safe_id(task_id)
        run_git(self._repo, "worktree", "remove", "--force", str(self._root / task_id))
