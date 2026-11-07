from pathlib import Path

from factory.worktrees import run_git


def clean_worktree(worktree: Path) -> str | None:
    """Remove every untracked and ignored file so the gate's tests see only what git recorded."""
    run_git(worktree, "clean", "-ffdxq")
    return None
