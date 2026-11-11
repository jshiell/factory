import re

_SPEC = re.compile(
    r"(?P<prefix>(?i:https://github\.com/|git@github\.com:))?"
    r"(?P<owner>[A-Za-z0-9._-]+)/(?P<name>[A-Za-z0-9._-]+?)(?:\.git)?"
)


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
    prefix = (match["prefix"] or "https://github.com/").lower()
    return f"{prefix}{owner}/{name}.git", name
