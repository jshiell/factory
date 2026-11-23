import ast
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from factory.process import run_to_exit
from factory.worktrees import WorktreeGit, git_completed, run_git

DEFAULT_PROTECTED_NAMES = ("conftest.py", "pytest.ini", ".pytest.ini", "pytest.toml", ".pytest.toml", "tox.ini", "setup.cfg", "uv.toml", ".python-version", ".python-versions")
# Build scripts a backend runs when the project is installed; they can declare dependencies and entry points.
DEPENDENCY_FILES = ("setup.py", "hatch_build.py")
# Everything else in these pyproject tables can add code or change what is installed or collected, so it is watched.
INERT_PROJECT_KEYS = (
    "name", "version", "description", "readme", "authors", "maintainers", "license", "license-files",
    "keywords", "classifiers", "urls", "requires-python",
)
INERT_TOOLS = ("ruff", "mypy", "black", "isort", "pyright", "coverage")
DEFAULT_TEST_GLOBS = ("test_*.py", "*_test.py")
_LINK_MODES = ("120000", "160000")
# Never editable, whatever the project or `allow_outside`; icase because a case-insensitive checkout folds them together.
_FORBIDDEN_PATHSPECS = (
    ":(top,literal,icase).factory", ":(top,literal,icase)factory.toml",
    ":(glob,icase)**/.gitattributes", ":(glob,icase)**/.gitmodules",  # git reads these: filters, submodule URLs
)
_FACTORY_IDENTITY = ("-c", "user.name=factory", "-c", "user.email=factory@localhost")


def commit_all(worktree: Path, message: str, git_dir: Path | None = None) -> bool:
    tree = WorktreeGit(worktree, git_dir)
    tree.git("add", "-A")
    if not tree.git("status", "--porcelain"):
        return False
    tree.git(*_FACTORY_IDENTITY, "-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", message)
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
    tracker=None,
    allow_outside: tuple[str, ...] = (),
    allow_dependency_changes: bool = False,
) -> GateResult:
    project_dir = _resolve_inside(worktree, project_subdir)
    if not _is_ancestor(worktree, base_sha):
        return GateResult(False, "not_descendant")
    if run_git(worktree, "rev-parse", "HEAD") == base_sha:
        return GateResult(False, "no_commits")
    if _forbidden_changed(worktree, base_sha):
        return GateResult(False, "outside_project")
    allowed_changes = [f":(top,literal){entry}" for entry in allow_outside]
    if not run_git(worktree, "diff", "--name-only", base_sha, "HEAD", "--", project_subdir, *allowed_changes):
        return GateResult(False, "no_changes")
    if (
        _tests_tampered(worktree, base_sha, project_subdir, test_globs)
        or _protected_changed(worktree, base_sha, project_subdir, protected_names, allow_dependency_changes)
        or (not allow_dependency_changes and _dependency_file_changed(worktree, base_sha))
    ):
        return GateResult(False, "tests_modified")
    if frozen_sha is not None:
        if not _is_ancestor(worktree, frozen_sha):
            return GateResult(False, "not_descendant")
        if _tests_changed_since(worktree, frozen_sha, project_subdir, test_globs):
            return GateResult(False, "tests_modified")
    if _changed_outside_project(worktree, base_sha, project_dir, allow_outside) or _link_added_under(
        worktree, base_sha, allow_outside
    ):
        return GateResult(False, "outside_project")
    try:
        ran = run_to_exit([*test_prefix, *test_argv], project_dir, env, timeout_s, capture_limit, tracker)
    except OSError:  # a missing sandbox wrapper: fail closed, never run the tests unconfined
        return GateResult(False, "sandbox_unavailable")
    if ran.timed_out:
        return GateResult(False, "tests_timeout")
    if ran.exit_code != 0:
        return GateResult(False, "tests_failed", ran.exit_code, ran.tail)
    return GateResult(True)


def _forbidden_changed(worktree: Path, base_sha: str) -> bool:
    return bool(run_git(
        worktree, "diff", "--name-only", "--no-renames", "--ignore-submodules=none", base_sha, "HEAD",
        "--", *_FORBIDDEN_PATHSPECS,
    ))


def _changed_outside_project(
    worktree: Path, base_sha: str, project_dir: Path, allow_outside: tuple[str, ...] = ()
) -> bool:
    """Anything beyond the project (root config, `uv.toml`, `sitecustomize.py`, a gitlink) can change what the tests run."""
    inside = project_dir.relative_to(worktree.resolve()).as_posix()
    allowed = [f":(top,exclude,literal){entry}" for entry in allow_outside]
    changed = run_git(
        worktree, "diff", "--name-only", "--no-renames", "--ignore-submodules=none", base_sha, "HEAD",
        "--", ".", f":(top,exclude,literal){inside}", *allowed,
    )
    return bool(changed)


def _link_added_under(worktree: Path, base_sha: str, allow_outside: tuple[str, ...]) -> bool:
    """A symlink or gitlink added or changed under an allowed path can point the tests at files the gate never sees."""
    if not allow_outside:
        return False
    raw = run_git(
        worktree, "diff", "--raw", "--no-renames", "--ignore-submodules=none", base_sha, "HEAD",
        "--", *(f":(top,literal){entry}" for entry in allow_outside),
    )
    return any(line.split()[1] in _LINK_MODES for line in raw.splitlines())


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
    shown = git_completed(worktree, "show", f"HEAD:{path}")
    try:
        tree = ast.parse(shown.stdout.decode())
    except (SyntaxError, ValueError):
        return False  # unparseable: pytest cannot collect it, so the gate fails anyway
    for scope in (tree, *(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef))):
        names = [n.name for n in scope.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")]
        if len(names) != len(set(names)):
            return True
    return False


def _dependency_file_changed(worktree: Path, base_sha: str) -> bool:
    pathspecs = [f":(glob,icase)**/{name}" for name in DEPENDENCY_FILES]
    if run_git(worktree, "diff", "--name-only", "--no-renames", base_sha, "HEAD", "--", *pathspecs):
        return True
    # An existing lock selects the installed versions; a new one only resolves pyproject.toml, which is watched.
    lock_changes = run_git(
        worktree, "diff", "--name-status", "--no-renames", base_sha, "HEAD", "--", ":(glob,icase)**/uv.lock"
    )
    return any(line[0] != "A" for line in lock_changes.splitlines())


def _protected_changed(
    worktree: Path, base_sha: str, project_subdir: str, protected_names: tuple[str, ...],
    allow_dependency_changes: bool = False,
) -> bool:
    pathspecs = [f":(glob,icase)**/{name}" for name in protected_names]  # pytest reads ini files above the project too
    if run_git(worktree, "diff", "--name-only", "--no-renames", base_sha, "HEAD", "--", *pathspecs):
        return True
    project = PurePosixPath(_resolve_inside(worktree, project_subdir).relative_to(worktree.resolve()).as_posix())
    changed = run_git(
        worktree, "diff", "--name-only", "-z", "--no-renames", base_sha, "HEAD", "--", ":(glob,icase)**/pyproject.toml"
    ).split("\0")
    return any(
        _config_tables(worktree, base_sha, path, allow_dependency_changes)
        != _config_tables(worktree, "HEAD", path, allow_dependency_changes)
        for path in changed
        if path and _reaches_project(PurePosixPath(path).parent, project)
    )


def _reaches_project(directory: PurePosixPath, project: PurePosixPath) -> bool:
    """A pyproject in the project, below it (a path dependency) or above it (pytest and uv read those too)."""
    return directory == project or project in directory.parents or directory in project.parents


def _config_tables(worktree: Path, revision: str, path: str, allow_dependency_changes: bool = False) -> tuple | None:
    """The parts of a pyproject that change what is installed or how the tests run (see INERT_*).
    With `allow_dependency_changes` only `[tool.pytest]` and `[tool.uv]` remain watched."""
    shown = git_completed(worktree, "show", f"{revision}:{path}")
    if shown.returncode != 0:
        return None
    try:
        document = tomllib.loads(shown.stdout.decode())
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return ({"unparseable": True},)
    tool = document.get("tool", {})
    if not isinstance(tool, dict):
        return ({"unparseable": True},)
    if allow_dependency_changes:
        return (tool.get("pytest"), tool.get("uv"))
    project = document.get("project")
    watched_project = (
        {key: value for key, value in project.items() if key not in INERT_PROJECT_KEYS}
        if isinstance(project, dict) else project  # a non-table value is compared as it stands
    )
    watched_tools = {name: table for name, table in tool.items() if name not in INERT_TOOLS}
    return (watched_project, document.get("build-system"), document.get("dependency-groups"), watched_tools)


def _is_ancestor(worktree: Path, base_sha: str) -> bool:
    completed = git_completed(worktree, "merge-base", "--is-ancestor", base_sha, "HEAD")
    return completed.returncode == 0


def _resolve_inside(worktree: Path, subdir: str) -> Path:
    resolved = (worktree / subdir).resolve()
    if not resolved.is_relative_to(worktree.resolve()) or resolved == worktree.resolve():
        raise ProjectSubdirEscapes(subdir)
    return resolved
