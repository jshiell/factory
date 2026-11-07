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


class ProjectSubdirEscapes(Exception):
    pass


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reason: str | None = None
    test_exit_code: int | None = None


def check_gate(
    worktree: Path, base_sha: str, project_subdir: str, test_argv: list[str], timeout_s: float
) -> GateResult:
    project_dir = _resolve_inside(worktree, project_subdir)
    if not _is_ancestor(worktree, base_sha):
        return GateResult(False, "not_descendant")
    if run_git(worktree, "rev-parse", "HEAD") == base_sha:
        return GateResult(False, "no_commits")
    if not run_git(worktree, "diff", "--name-only", base_sha, "HEAD", "--", project_subdir):
        return GateResult(False, "no_changes")
    if _tests_tampered(worktree, base_sha, project_subdir):
        return GateResult(False, "tests_modified")
    try:
        tests = subprocess.run(
            test_argv,
            cwd=project_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return GateResult(False, "tests_timeout")
    if tests.returncode != 0:
        return GateResult(False, "tests_failed", tests.returncode)
    return GateResult(True)


def _tests_tampered(worktree: Path, base_sha: str, project_subdir: str) -> bool:
    pathspecs = (f":(glob){project_subdir}/**/test_*.py", f":(glob){project_subdir}/**/*_test.py")
    statuses = run_git(worktree, "diff", "--name-status", "--no-renames", base_sha, "HEAD", "--", *pathspecs)
    if any(line[0] not in "AM" for line in statuses.splitlines()):
        return True
    numstat = run_git(worktree, "diff", "--numstat", "--no-renames", base_sha, "HEAD", "--", *pathspecs)
    return any(line.split("\t")[1] != "0" for line in numstat.splitlines())


def _is_ancestor(worktree: Path, base_sha: str) -> bool:
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", base_sha, "HEAD"],
        cwd=worktree, capture_output=True, stdin=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def _resolve_inside(worktree: Path, subdir: str) -> Path:
    resolved = (worktree / subdir).resolve()
    if not resolved.is_relative_to(worktree.resolve()) or resolved == worktree.resolve():
        raise ProjectSubdirEscapes(subdir)
    return resolved
