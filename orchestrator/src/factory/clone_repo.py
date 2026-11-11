import re

_SPEC = re.compile(r"(?:https://github\.com/)?(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?")


def parse_repository(spec: str) -> tuple[str, str]:
    match = _SPEC.fullmatch(spec)
    owner, name = match["owner"], match["name"]
    return f"https://github.com/{owner}/{name}.git", name
