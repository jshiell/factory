import subprocess
from pathlib import Path

from factory.worktrees import run_git


def clean_worktree(worktree: Path, factory_paths: tuple[Path, ...] = ()) -> str | None:
    """Remove every untracked and ignored file so the gate's tests see only what git recorded.

    Returns a gate failure reason, or None when nothing but tracked files remains."""
    if not _safe_to_clean(worktree, factory_paths):
        return "worktree_mismatch"
    try:
        run_git(worktree, "clean", "-ffdxq")
    except subprocess.CalledProcessError:
        pass  # judged by what is left, not by the exit code
    status = run_git(worktree, "status", "--porcelain", "--ignored", "--untracked-files=all")
    if any(line.startswith(("??", "!!")) for line in status.splitlines()):
        return "worktree_not_clean"
    return None


def _safe_to_clean(worktree: Path, factory_paths: tuple[Path, ...]) -> bool:
    """The agent can write the worktree's `.git` file: never run a deleting command on a repository it redirected."""
    root = worktree.resolve()
    if any(path.resolve().is_relative_to(root) for path in factory_paths):
        return False
    try:
        toplevel = Path(run_git(worktree, "rev-parse", "--show-toplevel")).resolve()
        git_dir = Path(run_git(worktree, "rev-parse", "--absolute-git-dir")).resolve()
    except subprocess.CalledProcessError:
        return False
    if toplevel != root:
        return False
    return not git_dir.is_relative_to(root) or git_dir == root / ".git"
