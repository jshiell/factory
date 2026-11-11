import re
import subprocess

from factory.settings import harness_home

_SPEC = re.compile(
    r"(?P<prefix>(?i:https://github\.com/|git@github\.com:))?"
    r"(?P<owner>[A-Za-z0-9._-]+)/(?P<name>[A-Za-z0-9._-]+?)(?:\.git)?"
)


_RESERVED = ("_shared", "factory.toml")


class RepositoryError(ValueError):
    pass


def parse_repository(spec: str) -> tuple[str, str]:
    match = _SPEC.fullmatch(spec)
    if match is None:
        raise RepositoryError(f"not a GitHub repository: {spec}")
    owner, name = match["owner"], match["name"]
    for part in (owner, name):
        if part in (".", "..", ".git") or part.startswith("-"):
            raise RepositoryError(f"not a usable repository name: {part}")
    if name.casefold() in _RESERVED:
        raise RepositoryError(f"{name} is reserved in the harness home")
    prefix = (match["prefix"] or "https://github.com/").lower()
    return f"{prefix}{owner}/{name}.git", name


def clone_repository(spec: str, io) -> int:
    url, name = parse_repository(spec)
    destination = harness_home() / name / "repo"
    if destination.exists():
        print(f"{destination} already exists; not overwriting", file=io.stderr)
        return 1
    subprocess.run(["git", "clone", "--", url, str(destination)], stdin=subprocess.DEVNULL, check=True)
    print(f"cloned {url} into {destination}", file=io.stdout)
    return 0
