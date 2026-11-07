import ast
import os
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from factory.process import run_to_exit
from factory.worktrees import HARDENED_GIT, run_git

DEFAULT_PROTECTED_NAMES = ("conftest.py", "pytest.ini", ".pytest.ini", "pytest.toml", ".pytest.toml", "tox.ini", "setup.cfg")
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
    output: str | None = None


def check_gate(
    worktree: Path, base_sha: str, project_subdir: str, test_argv: list[str], timeout_s: float,
    test_globs: tuple[str, ...] = DEFAULT_TEST_GLOBS,
    protected_names: tuple[str, ...] = DEFAULT_PROTECTED_NAMES,
    env: dict[str, str] | None = None,
    test_prefix: tuple[str, ...] = (),
    capture_limit: int | None = None,
    frozen_sha: str | None = None,
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
    if frozen_sha is not None:
        if not _is_ancestor(worktree, frozen_sha):
            return GateResult(False, "not_descendant")
        if _tests_changed_since(worktree, frozen_sha, project_subdir, test_globs):
            return GateResult(False, "tests_modified")
    if _changed_outside_project(worktree, base_sha, project_dir):
        return GateResult(False, "outside_project")
    try:
        ran = run_to_exit([*test_prefix, *test_argv], project_dir, env, timeout_s, capture_limit)
    except OSError:  # a missing sandbox wrapper: fail closed, never run the tests unconfined
        return GateResult(False, "sandbox_unavailable")
    if ran.timed_out:
        return GateResult(False, "tests_timeout")
    if ran.exit_code != 0:
        return GateResult(False, "tests_failed", ran.exit_code, ran.tail)
    return GateResult(True)


def _changed_outside_project(worktree: Path, base_sha: str, project_dir: Path) -> bool:
    """Anything beyond the project (root config, `uv.toml`, `sitecustomize.py`, a gitlink) can change what the tests run."""
    inside = project_dir.relative_to(worktree.resolve()).as_posix()
    changed = run_git(
        worktree, "diff", "--name-only", "--no-renames", "--ignore-submodules=none", base_sha, "HEAD",
        "--", ".", f":(top,exclude,literal){inside}",
    )
    return bool(changed)


def _tests_changed_since(worktree: Path, frozen_sha: str, project_subdir: str, test_globs: tuple[str, ...]) -> bool:
    """Since a fix round began, test files may only be added: the base comparison cannot see edits to the agent's own tests."""
    pathspecs = tuple(f":(glob){project_subdir}/**/{glob}" for glob in test_globs)
    statuses = run_git(worktree, "diff", "--name-status", "--no-renames", frozen_sha, "HEAD", "--", *pathspecs)
    return any(line[0] != "A" for line in statuses.splitlines())


def _tests_tampered(worktree: Path, base_sha: str, project_subdir: str, test_globs: tuple[str, ...]) -> bool:
    pathspecs = tuple(f":(glob){project_subdir}/**/{glob}" for glob in test_globs)
    statuses = run_git(worktree, "diff", "--name-status", "--no-renames", base_sha, "HEAD", "--", *pathspecs)
    if any(line[0] not in "AM" for line in statuses.splitlines()):
        return True
    numstat = run_git(worktree, "diff", "--numstat", "--no-renames", base_sha, "HEAD", "--", *pathspecs)
    if any(line.split("\t")[1] != "0" for line in numstat.splitlines()):
        return True
    modified = [line.split("\t", 1)[1] for line in statuses.splitlines() if line[0] == "M"]
    return any(_defines_a_test_twice(worktree, path) for path in modified)


def _defines_a_test_twice(worktree: Path, path: str) -> bool:
    """A later `def test_x` silently replaces an earlier one: appending it disables an existing test."""
    shown = subprocess.run(
        [*HARDENED_GIT, "show", f"HEAD:{path}"], cwd=worktree, capture_output=True, stdin=subprocess.DEVNULL
    )
    try:
        tree = ast.parse(shown.stdout.decode())
    except (SyntaxError, ValueError):
        return False  # unparseable: pytest cannot collect it, so the gate fails anyway
    for scope in (tree, *(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef))):
        names = [n.name for n in scope.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")]
        if len(names) != len(set(names)):
            return True
    return False


def _protected_changed(
    worktree: Path, base_sha: str, project_subdir: str, protected_names: tuple[str, ...]
) -> bool:
    pathspecs = [f":(glob)**/{name}" for name in protected_names]  # pytest reads ini files above the project too
    if run_git(worktree, "diff", "--name-only", "--no-renames", base_sha, "HEAD", "--", *pathspecs):
        return True
    project = _resolve_inside(worktree, project_subdir).relative_to(worktree.resolve())
    pyprojects = [(directory / "pyproject.toml").as_posix() for directory in (project, *project.parents)]
    return any(
        _pytest_table(worktree, base_sha, pyproject) != _pytest_table(worktree, "HEAD", pyproject)
        for pyproject in pyprojects
    )


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
    tool = document.get("tool", {})
    table = tool.get("pytest") if isinstance(tool, dict) else {"unparseable": True}
    return table if table is None or isinstance(table, dict) else {"unparseable": True}


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
