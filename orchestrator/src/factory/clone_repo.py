import re
import subprocess
from pathlib import Path

from factory.settings import harness_home

_SPEC = re.compile(
    r"(?P<prefix>(?i:https://github\.com/|git@github\.com:))?"
    r"(?P<owner>[A-Za-z0-9._-]+)/(?P<name>[A-Za-z0-9._-]+?)(?:\.git)?"
)


_RESERVED = ("_shared", "factory.toml")


class RepositoryError(ValueError):
    pass


def github_slug(spec: str) -> tuple[str, str]:
    match = _SPEC.fullmatch(spec)
    if match is None:
        raise RepositoryError(f"not a GitHub repository: {spec}")
    owner, name = match["owner"], match["name"]
    for part in (owner, name):
        if part in (".", "..", ".git") or part.startswith("-"):
            raise RepositoryError(f"not a usable repository name: {part}")
    if name.casefold() in _RESERVED:
        raise RepositoryError(f"{name} is reserved in the harness home")
    return owner, name


def parse_repository(spec: str) -> tuple[str, str]:
    owner, name = github_slug(spec)
    prefix = (_SPEC.fullmatch(spec)["prefix"] or "https://github.com/").lower()
    return f"{prefix}{owner}/{name}.git", name


def gh_target(spec: str) -> str:
    """What `gh repo clone` is given: a bare slug with the host pinned, so gh picks the protocol;
    a spec typed with a prefix keeps its protocol as the canonical URL."""
    if _SPEC.fullmatch(spec) and _SPEC.fullmatch(spec)["prefix"]:
        return parse_repository(spec)[0]
    owner, name = github_slug(spec)
    return f"github.com/{owner}/{name}"


def clone_repository(spec: str, io) -> int:
    url, name = parse_repository(spec)
    destination = harness_home() / name / "repo"
    if destination.exists():
        print(f"{destination} already exists; not overwriting", file=io.stderr)
        return 1
    created = _missing_ancestors(destination)
    completed = subprocess.run(["git", "clone", "--", url, str(destination)], stdin=subprocess.DEVNULL)
    if completed.returncode != 0:
        for directory in created:
            _remove_if_empty(directory)
        return 1
    print(f"cloned {url} into {destination}", file=io.stdout)
    return 0


def _missing_ancestors(path: Path) -> list[Path]:
    """The directories above path that do not exist yet, deepest first."""
    return [parent for parent in path.parents if not parent.exists()]


def _remove_if_empty(directory: Path) -> None:
    try:
        directory.rmdir()
    except OSError:
        pass
