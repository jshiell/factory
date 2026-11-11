import subprocess
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath


class SettingsError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    repo: Path
    state_dir: Path
    worktree_root: Path
    project_subdir: str = "target"
    test_argv: tuple[str, ...] = ("uv", "run", "pytest", "-q")
    cap_usd: float = 5.0
    gate_timeout_s: float = 120.0
    runner_timeout_s: float = 300.0
    claude_executable: str = "claude"
    sandbox_executable: str = "nono"
    model: str = "sonnet"
    budget_usd: float = 0.5
    allowed_tools: tuple[str, ...] = ()
    max_turns: int = 30
    fix_rounds: int = 2
    server_port: int = 8765

    @property
    def ledger_path(self) -> Path:
        return self.state_dir / "ledger.jsonl"

    @property
    def gate_cache_dir(self) -> Path:
        return self.state_dir / "gate-cache"

    @property
    def db_path(self) -> Path:
        return self.state_dir / "factory.db"

    @property
    def tasks_dir(self) -> Path:
        return self.state_dir / "tasks"


_PATH_KEYS = ("repo", "state_dir", "worktree_root")
_STRING_KEYS = ("project_subdir", "claude_executable", "sandbox_executable", "model")
_FLOAT_KEYS = ("cap_usd", "gate_timeout_s", "runner_timeout_s", "budget_usd")
_STRING_LIST_KEYS = ("test_argv", "allowed_tools")
_INT_KEYS = ("max_turns", "fix_rounds", "server_port")
_KNOWN = _PATH_KEYS + _STRING_KEYS + _FLOAT_KEYS + _STRING_LIST_KEYS + _INT_KEYS


def load_settings(path: Path | None, cwd: Path) -> Settings:
    if path is None and (cwd / "factory.toml").exists():
        path = cwd / "factory.toml"
    values, base = ({}, cwd) if path is None else (_read(path), path.parent)
    unknown = sorted(set(values) - set(_KNOWN))
    if unknown:
        raise SettingsError(f"unknown setting: {unknown[0]}")
    typed = {key: _typed(key, value) for key, value in values.items()}
    for key in _PATH_KEYS:
        if key in typed:
            typed[key] = base / typed[key]
    repo = typed.pop("repo", None) or _git_toplevel(cwd)
    state_dir = typed.pop("state_dir", None) or _project_dir(repo) / "state"
    worktree_root = typed.pop("worktree_root", None) or _project_dir(repo) / "worktrees"
    settings = replace(Settings(repo, state_dir, worktree_root), **typed)
    _check_project_subdir(settings.project_subdir)
    return settings


def _project_dir(repo: Path) -> Path:
    return Path.home() / ".harness" / repo.resolve().name


def _read(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text())
    except FileNotFoundError:
        raise SettingsError(f"config file not found: {path}") from None
    except tomllib.TOMLDecodeError as error:
        raise SettingsError(f"invalid TOML in {path}: {error}") from None


def _typed(key: str, value):
    if key in _PATH_KEYS or key in _STRING_KEYS:
        if isinstance(value, str):
            return value
    elif key in _FLOAT_KEYS:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    elif key in _INT_KEYS:
        if isinstance(value, int) and not isinstance(value, bool) and (value >= 0 or key == "max_turns"):
            return value
    elif isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise SettingsError(f"wrong type for setting: {key}")


def _check_project_subdir(subdir: str) -> None:
    parts = PurePosixPath(subdir).parts
    if not subdir or PurePosixPath(subdir).is_absolute() or ".." in parts:
        raise SettingsError("project_subdir must be a relative path without '..'")


def _git_toplevel(cwd: Path) -> Path:
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=cwd, capture_output=True, text=True,
        stdin=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        raise SettingsError("repo not set and the current directory is not in a git repository")
    return Path(completed.stdout.strip())
