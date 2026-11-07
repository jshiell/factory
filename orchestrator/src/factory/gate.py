import os
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from factory.process import run_to_exit
from factory.worktrees import HARDENED_GIT, run_git

DEFAULT_PROTECTED_NAMES = ("conftest.py", "pytest.ini", "tox.ini", "setup.cfg")
DEFAULT_TEST_GLOBS = ("test_*.py", "*_test.py")
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


def has_nested_repo(worktree: Path, project_subdir: str) -> bool:
    """A `.git` file or directory anywhere under the project: agent-controlled config git must never read."""
    for _, dirnames, filenames in os.walk(_resolve_inside(worktree, project_subdir)):
        if ".git" in dirnames or ".git" in filenames:
            return True
    return False


class ProjectSubdirEscapes(Exception):
    pass


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reason: str | None = None
    test_exit_code: int | None = None


def check_gate(
    worktree: Path, base_sha: str, project_subdir: str, test_argv: list[str], timeout_s: float,
    test_globs: tuple[str, ...] = DEFAULT_TEST_GLOBS,
    protected_names: tuple[str, ...] = DEFAULT_PROTECTED_NAMES,
    env: dict[str, str] | None = None,
    test_prefix: tuple[str, ...] = (),
) -> GateResult:
    project_dir = _resolve_inside(worktree, project_subdir)
    if not _is_ancestor(worktree, base_sha):
        return GateResult(False, "not_descendant")
    if run_git(worktree, "rev-parse", "HEAD") == base_sha:
        return GateResult(False, "no_commits")
    if not run_git(worktree, "diff", "--name-only", base_sha, "HEAD", "--", project_subdir):
        return GateResult(False, "no_changes")
    if _tests_tampered(worktree, base_sha, project_subdir, test_globs) or _protected_changed(
        worktree, base_sha, project_subdir, protected_names
    ):
        return GateResult(False, "tests_modified")
    try:
        exit_code, timed_out = run_to_exit([*test_prefix, *test_argv], project_dir, env, timeout_s)
    except OSError:  # a missing sandbox wrapper: fail closed, never run the tests unconfined
        return GateResult(False, "sandbox_unavailable")
    if timed_out:
        return GateResult(False, "tests_timeout")
    if exit_code != 0:
        return GateResult(False, "tests_failed", exit_code)
    return GateResult(True)


def _tests_tampered(worktree: Path, base_sha: str, project_subdir: str, test_globs: tuple[str, ...]) -> bool:
    pathspecs = tuple(f":(glob){project_subdir}/**/{glob}" for glob in test_globs)
    statuses = run_git(worktree, "diff", "--name-status", "--no-renames", base_sha, "HEAD", "--", *pathspecs)
    if any(line[0] not in "AM" for line in statuses.splitlines()):
        return True
    numstat = run_git(worktree, "diff", "--numstat", "--no-renames", base_sha, "HEAD", "--", *pathspecs)
    return any(line.split("\t")[1] != "0" for line in numstat.splitlines())


def _protected_changed(
    worktree: Path, base_sha: str, project_subdir: str, protected_names: tuple[str, ...]
) -> bool:
    pathspecs = [f":(glob){project_subdir}/**/{name}" for name in protected_names]
    if run_git(worktree, "diff", "--name-only", "--no-renames", base_sha, "HEAD", "--", *pathspecs):
        return True
    pyproject = f"{project_subdir}/pyproject.toml"
    return _pytest_table(worktree, base_sha, pyproject) != _pytest_table(worktree, "HEAD", pyproject)


def _pytest_table(worktree: Path, revision: str, path: str) -> dict | None:
    shown = subprocess.run(
        [*HARDENED_GIT, "show", f"{revision}:{path}"], cwd=worktree, capture_output=True, stdin=subprocess.DEVNULL
    )
    if shown.returncode != 0:
        return None
    try:
        document = tomllib.loads(shown.stdout.decode())
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {"unparseable": True}
    return document.get("tool", {}).get("pytest", {}).get("ini_options")


def _is_ancestor(worktree: Path, base_sha: str) -> bool:
    completed = subprocess.run(
        [*HARDENED_GIT, "merge-base", "--is-ancestor", base_sha, "HEAD"],
        cwd=worktree, capture_output=True, stdin=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def _resolve_inside(worktree: Path, subdir: str) -> Path:
    resolved = (worktree / subdir).resolve()
    if not resolved.is_relative_to(worktree.resolve()) or resolved == worktree.resolve():
        raise ProjectSubdirEscapes(subdir)
    return resolved
