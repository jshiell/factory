from pathlib import Path

from factory.worktrees import run_git

_FACTORY_IDENTITY = ("-c", "user.name=factory", "-c", "user.email=factory@localhost")


def commit_all(worktree: Path, message: str) -> bool:
    run_git(worktree, "add", "-A")
    if not run_git(worktree, "status", "--porcelain"):
        return False
    run_git(
        worktree, *_FACTORY_IDENTITY, "-c", "commit.gpgsign=false",
        "commit", "-q", "--no-verify", "-m", message,
    )
    return True
