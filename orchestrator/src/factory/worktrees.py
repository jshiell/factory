import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from factory.ids import require_safe_id

# The worktree is agent-controlled: never let its config run commands (fsmonitor, hooks) in the factory's git.
HARDENED_GIT = ("git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-c", "diff.ignoreSubmodules=all")


# The user's own git config (filters such as git-lfs, textconv, merge drivers) must not run on agent-written files.
NO_USER_CONFIG_OVERRIDES = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
_INJECTED_CONFIG = ("GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS")


def hardened_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = {**(os.environ if base is None else base), **NO_USER_CONFIG_OVERRIDES}
    return {k: v for k, v in env.items() if k not in _INJECTED_CONFIG and not k.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))}


def run_git(cwd: Path, *args: str, env: dict[str, str] | None = None, user_config: bool = False) -> str:
    """Run hardened git. The user's global and system config is ignored unless `user_config` (only for the user's own checkout)."""
    result = subprocess.run(
        [*HARDENED_GIT, *args], cwd=cwd, capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL,
        env=env if user_config else hardened_env(env),
    )
    return result.stdout.strip()


class WorktreeMismatch(Exception):
    """A task worktree's `.git` does not point at the admin dir git made for it."""


@dataclass(frozen=True)
class WorktreeGit:
    """Git over a task's worktree. Pinned (`git_dir` set), every call names its git-dir and work-tree itself: the agent-writable `.git` file is never read by git."""
    path: Path
    git_dir: Path | None = None

    def git(self, *args: str) -> str:
        pin = (f"--git-dir={self.git_dir}", f"--work-tree={self.path}") if self.git_dir else ()
        return run_git(self.path, *pin, *args)


def linked_git_dir(repo: Path, task_id: str, worktree: Path) -> Path:
    """The linked worktree's admin dir inside the main repo, accepted only if the worktree's `.git` file says so."""
    expected = (repo / run_git(repo, "rev-parse", "--git-common-dir")).resolve() / "worktrees" / task_id
    gitfile = worktree / ".git"
    if gitfile.is_symlink() or not gitfile.is_file():
        raise WorktreeMismatch("the worktree's .git is not a plain file: refusing to trust its gitdir")
    try:  # agent-written: any content, any size
        with gitfile.open("rb") as handle:
            claimed = handle.read(4096).decode().strip().removeprefix("gitdir:").strip()
        matches = expected.is_dir() and Path(claimed).resolve() == expected
    except (OSError, UnicodeDecodeError, ValueError):
        claimed, matches = "unreadable", False
    if not matches:
        raise WorktreeMismatch(f"the worktree's gitdir ({claimed}) is not {expected}")
    return expected


def worktree_git(worktree: Path, task_id: str, repo: Path | None) -> WorktreeGit:
    """Pinned to the admin dir `repo` made for the task (raises WorktreeMismatch); without a repo (eval audit, unit tests), git discovers it as before and a rewritten root `.git` goes unchecked."""
    return WorktreeGit(worktree, None if repo is None else linked_git_dir(repo, task_id, worktree))


def git_completed(cwd: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """Hardened git whose exit code and raw (bytes) output the caller inspects itself."""
    return subprocess.run([*HARDENED_GIT, *args], cwd=cwd, capture_output=True, stdin=subprocess.DEVNULL, env=hardened_env())


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    """True if `ancestor` is `descendant` or reachable from it. Only git's "no" (exit 1) is False; any other error raises."""
    try:
        run_git(repo, "merge-base", "--is-ancestor", ancestor, descendant)
    except subprocess.CalledProcessError as error:
        if error.returncode == 1:
            return False
        raise
    return True


def branch_problem(repo: Path, main_branch: str | None = None) -> str | None:
    """Why the main checkout's branch is wrong: detached, or (when `main_branch` is set) another branch. None when fine."""
    branch = run_git(repo, "branch", "--show-current")
    if not branch:
        return "main is not on a branch"
    if main_branch is not None and branch != main_branch:
        return f"main is on branch {branch}, not {main_branch} (main_branch in .factory/factory.toml)"
    return None


def main_checkout_problem(repo: Path, main_branch: str | None = None) -> str | None:
    """Why the main checkout cannot take a merge or rebase: wrong or no branch, or uncommitted tracked changes. None when fine."""
    if problem := branch_problem(repo, main_branch):
        return problem
    if run_git(repo, "status", "--porcelain", "--untracked-files=no"):
        return "main has uncommitted changes"
    return None


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
        run_git(self._repo, "worktree", "add", "-q", "-b", branch, str(path), base_sha, user_config=True)
        return path, base_sha

    def remove(self, task_id: str) -> None:
        require_safe_id(task_id)
        run_git(self._repo, "worktree", "remove", "--force", str(self._root / task_id))
