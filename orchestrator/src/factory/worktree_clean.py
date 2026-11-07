import subprocess
from pathlib import Path

from factory.worktrees import run_git


def clean_worktree(worktree: Path) -> str | None:
    """Remove every untracked and ignored file so the gate's tests see only what git recorded.

    Returns a gate failure reason, or None when nothing but tracked files remains."""
    try:
        run_git(worktree, "clean", "-ffdxq")
    except subprocess.CalledProcessError:
        pass  # judged by what is left, not by the exit code
    status = run_git(worktree, "status", "--porcelain", "--ignored", "--untracked-files=all")
    if any(line.startswith(("??", "!!")) for line in status.splitlines()):
        return "worktree_not_clean"
    return None
