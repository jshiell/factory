def parse_repository(spec: str) -> tuple[str, str]:
    owner, name = spec.split("/")
    return f"https://github.com/{owner}/{name}.git", name
