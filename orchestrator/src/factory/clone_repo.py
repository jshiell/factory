import re

_SPEC = re.compile(
    r"(?P<prefix>https://github\.com/|git@github\.com:)?(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?"
)


class RepositoryError(ValueError):
    pass


def parse_repository(spec: str) -> tuple[str, str]:
    match = _SPEC.fullmatch(spec)
    owner, name = match["owner"], match["name"]
    for part in (owner, name):
        if part in (".", "..", ".git") or part.startswith("-"):
            raise RepositoryError(f"not a usable repository name: {part}")
    prefix = match["prefix"] or "https://github.com/"
    return f"{prefix}{owner}/{name}.git", name
