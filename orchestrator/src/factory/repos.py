import re
from dataclasses import dataclass
from pathlib import Path

RESERVED_NAMES = ("_shared", "_server", "factory.toml")
_NAME = re.compile(r"[A-Za-z0-9._-]+")


@dataclass(frozen=True)
class Discovery:
    names: tuple[str, ...]
    problems: dict[str, str]  # directory name -> why it is not served


class UnknownRepo(KeyError):
    def __str__(self) -> str:
        return self.args[0]


class RepoRegistry:
    """The repos a server started (name -> its context) and those it skipped (name -> why)."""

    def __init__(self, started: dict, skipped: dict[str, str]):
        self._started = dict(started)
        self.skipped = dict(skipped)

    @property
    def started_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._started))

    def get(self, name: str):
        if name in self._started:
            return self._started[name]
        if name in self.skipped:
            raise UnknownRepo(f"repo {name} was skipped: {self.skipped[name]}")
        known = f"known: {', '.join(self.started_names)}" if self._started else "no repos"
        raise UnknownRepo(f"unknown repo {name}; {known}")


def name_problem(name: str) -> str | None:
    """Why `name` cannot be a repo name, or None."""
    if name.casefold() in RESERVED_NAMES:
        return f"the name {name} is reserved in the factory home"
    if not _NAME.fullmatch(name) or name[0] in ".-":
        return f"the name {name} must match [A-Za-z0-9._-]+ and not start with . or -"
    return None


def discover_repos(home: Path) -> Discovery:
    """The repos at `<home>/<name>/repo`. `home` itself may be a symlink; nothing below it may be."""
    if not home.is_dir():
        return Discovery((), {})
    names: list[str] = []
    problems: dict[str, str] = {}
    for entry in sorted(home.iterdir(), key=lambda path: path.name):
        project, repo = entry, entry / "repo"
        if not (project.is_symlink() or project.is_dir()) or not (repo.is_symlink() or repo.exists()):
            continue
        if problem := name_problem(entry.name):
            problems[entry.name] = problem
        elif project.is_symlink() or repo.is_symlink():
            problems[entry.name] = f"{entry} is a symlink; use the real directory"
        else:
            names.append(entry.name)
    return Discovery(tuple(names), problems)


def repo_name_from_cwd(home: Path, cwd: Path) -> str | None:
    """The repo the directory belongs to: its project directory, its checkout at any depth, or one of its task worktrees."""
    try:
        parts = cwd.resolve().relative_to(home.resolve()).parts
    except ValueError:
        return None
    if not parts or name_problem(parts[0]):
        return None
    inside = parts[1:]
    if not inside or inside[0] == "repo" or (inside[0] == "worktrees" and len(inside) > 1):
        return parts[0]
    return None
