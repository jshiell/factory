from pathlib import Path


def config_path(repo: Path) -> Path:
    return repo / "factory.toml"


def write_config(repo: Path, text: str) -> Path:
    path = config_path(repo)
    path.write_text(text)
    return path
