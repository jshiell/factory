import subprocess
from dataclasses import dataclass
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


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reason: str | None = None
    test_exit_code: int | None = None


def check_gate(
    worktree: Path, base_sha: str, project_subdir: str, test_argv: list[str], timeout_s: float
) -> GateResult:
    if not _is_ancestor(worktree, base_sha):
        return GateResult(False, "not_descendant")
    if run_git(worktree, "rev-parse", "HEAD") == base_sha:
        return GateResult(False, "no_commits")
    if not run_git(worktree, "diff", "--name-only", base_sha, "HEAD", "--", project_subdir):
        return GateResult(False, "no_changes")
    tests = subprocess.run(
        test_argv, cwd=worktree / project_subdir, capture_output=True, stdin=subprocess.DEVNULL
    )
    if tests.returncode != 0:
        return GateResult(False, "tests_failed", tests.returncode)
    return GateResult(True)


def _is_ancestor(worktree: Path, base_sha: str) -> bool:
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", base_sha, "HEAD"],
        cwd=worktree, capture_output=True, stdin=subprocess.DEVNULL,
    )
    return completed.returncode == 0
