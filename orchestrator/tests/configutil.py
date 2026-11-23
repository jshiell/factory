from pathlib import Path


def config_path(repo: Path) -> Path:
    return repo / ".factory" / "factory.toml"


def write_config(repo: Path, text: str) -> Path:
    path = config_path(repo)
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    return path
