import ast
import os
import tomllib
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePath

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
# Never editable, whatever the project; icase because a case-insensitive checkout folds them together.
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
    """A `.git` file or directory anywhere below the worktree root: agent-controlled config git must never read.
    The root `.git` is the worktree's own; the caller has already checked it names the expected gitdir."""
    _resolve_inside(worktree, project_subdir)  # refuse an escaping subdir before anything is committed
    walk = os.walk(worktree)
    for _, root_dirnames, _ in walk:
        if ".git" in root_dirnames:
            root_dirnames.remove(".git")  # never descend into the worktree's own git directory
        break
    return any(".git" in dirnames or ".git" in filenames for _, dirnames, filenames in walk)


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
    allow_dependency_changes: bool = False,
) -> GateResult:
    project_dir = _resolve_inside(worktree, project_subdir)
    if not _is_ancestor(worktree, base_sha):
        return GateResult(False, "not_descendant")
    if run_git(worktree, "rev-parse", "HEAD") == base_sha:
        return GateResult(False, "no_commits")
    if _forbidden_changed(worktree, base_sha):
        return GateResult(False, "outside_project")
    if not run_git(worktree, "diff", "--name-only", base_sha, "HEAD", "--", ":/"):  # anywhere in the repo
        return GateResult(False, "no_changes")
    if _tests_tampered(worktree, base_sha, test_globs):
        return GateResult(False, "tests_modified")
    if (
        _protected_changed(worktree, base_sha, protected_names, allow_dependency_changes, test_globs)
        or (not allow_dependency_changes and _dependency_file_changed(worktree, base_sha))
    ):
        return GateResult(False, "protected_file_changed")
    if frozen_sha is not None:
        if not _is_ancestor(worktree, frozen_sha):
            return GateResult(False, "not_descendant")
        if _tests_changed_since(worktree, frozen_sha, test_globs):
            return GateResult(False, "tests_modified")
    if _link_added(worktree, base_sha):
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


def _link_added(worktree: Path, base_sha: str) -> bool:
    """A symlink or gitlink added or changed anywhere can point the tests at files the gate never sees."""
    raw = run_git(worktree, "diff", "--raw", "--no-renames", "--ignore-submodules=none", base_sha, "HEAD")
    return any(line.split()[1] in _LINK_MODES for line in raw.splitlines())


def _tests_changed_since(worktree: Path, frozen_sha: str, test_globs: tuple[str, ...]) -> bool:
    """Since a fix round began, test files may only be added: the base comparison cannot see edits to the agent's own tests."""
    statuses = run_git(
        worktree, "diff", "--name-status", "--no-renames", frozen_sha, "HEAD", "--", *_test_pathspecs(test_globs)
    )
    return any(line[0] != "A" for line in statuses.splitlines())


def _test_pathspecs(test_globs: tuple[str, ...]) -> tuple[str, ...]:
    """Every test in the repo: the agent may edit outside the project, and the project may collect tests there."""
    return tuple(f":(glob)**/{glob}" for glob in test_globs)


def _tests_tampered(worktree: Path, base_sha: str, test_globs: tuple[str, ...]) -> bool:
    pathspecs = _test_pathspecs(test_globs)
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
    worktree: Path, base_sha: str, protected_names: tuple[str, ...],
    allow_dependency_changes: bool = False,
    test_globs: tuple[str, ...] = DEFAULT_TEST_GLOBS,
) -> bool:
    pathspecs = [f":(glob,icase)**/{name}" for name in protected_names]  # pytest reads ini files above the project too
    if run_git(worktree, "diff", "--name-only", "--no-renames", base_sha, "HEAD", "--", *pathspecs):
        return True
    changed = run_git(
        worktree, "diff", "--name-only", "-z", "--no-renames", base_sha, "HEAD", "--", ":(glob,icase)**/pyproject.toml"
    ).split("\0")
    new_config_is_harmless = not _has_tests(worktree, base_sha, test_globs)
    return any(
        _config_changed(worktree, base_sha, path, allow_dependency_changes, new_config_is_harmless)
        for path in changed
        if path  # every pyproject: the agent may edit any, and one outside the project can still reach it
    )


def _has_tests(worktree: Path, revision: str, test_globs: tuple[str, ...]) -> bool:
    names = run_git(worktree, "ls-tree", "-r", "--name-only", "-z", revision).split("\0")
    return any(fnmatch(PurePath(name).name, glob) for name in names if name for glob in test_globs)


def _config_changed(
    worktree: Path, base_sha: str, path: str, allow_dependency_changes: bool, new_config_is_harmless: bool,
) -> bool:
    before = _config_tables(worktree, base_sha, path, allow_dependency_changes)
    if before is None:
        return not new_config_is_harmless  # a new pyproject can only hide tests that exist
    return before != _config_tables(worktree, "HEAD", path, allow_dependency_changes)


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
    root = worktree.resolve()
    resolved = (worktree / subdir).resolve()
    if not resolved.is_relative_to(root) or in_factory_dir(resolved.relative_to(root)):
        raise ProjectSubdirEscapes(subdir)
    return resolved


def in_factory_dir(relative: PurePath) -> bool:
    return relative.parts[:1] != () and relative.parts[0].casefold() == ".factory"  # casefold: a case-insensitive checkout is the same dir
