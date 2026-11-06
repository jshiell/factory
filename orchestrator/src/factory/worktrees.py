import subprocess
from pathlib import Path

from factory.ids import require_safe_id


def run_git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL
    )
    return result.stdout.strip()


class WorktreeManager:
    def __init__(self, repo: Path, root: Path):
        self._repo = repo
        self._root = root

    def create(self, task_id: str) -> tuple[Path, str]:
        require_safe_id(task_id)
        base_sha = run_git(self._repo, "rev-parse", "HEAD")
        path = self._root / task_id
        run_git(self._repo, "worktree", "add", "-q", "-b", f"factory/{task_id}", str(path), base_sha)
        return path, base_sha
