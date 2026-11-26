import re
from dataclasses import dataclass
from pathlib import Path

RESERVED_NAMES = ("_shared", "_server", "factory.toml")
_NAME = re.compile(r"[A-Za-z0-9._-]+")


@dataclass(frozen=True)
class Discovery:
    names: tuple[str, ...]
    problems: dict[str, str]  # directory name -> why it is not served


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
