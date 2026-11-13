import json
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, field, replace
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
    append_system_prompt: str | None = None
    stage_prompts: dict[str, str] = field(default_factory=dict)
    plugins: tuple[str, ...] = ()
    plugin_dirs: tuple[Path, ...] = ()
    stage_plugins: dict[str, tuple[str, ...]] = field(default_factory=dict)
    stage_plugin_dirs: dict[str, tuple[Path, ...]] = field(default_factory=dict)
    watch_approvers: tuple[str, ...] = ()

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


_PATH_KEYS = ("repo", "state_dir", "worktree_root", "home")
_STRING_KEYS = ("project_subdir", "claude_executable", "sandbox_executable", "model")
_PROMPT_KEYS = ("append_system_prompt",)
_STAGES = ("plan", "review", "implement", "audit")
_FLOAT_KEYS = ("cap_usd", "gate_timeout_s", "runner_timeout_s", "budget_usd")
_STRING_LIST_KEYS = ("test_argv", "allowed_tools", "plugins", "watch_approvers")
_INT_KEYS = ("max_turns", "fix_rounds", "server_port")
GITHUB_LOGIN = re.compile(r"(?!.{40})[A-Za-z0-9]+(-[A-Za-z0-9]+)*")
_KNOWN =_PATH_KEYS + _STRING_KEYS + _PROMPT_KEYS + ("stage_prompts", "stage_plugins") + _FLOAT_KEYS + _STRING_LIST_KEYS + _INT_KEYS


def load_settings(path: Path | None, cwd: Path) -> Settings:
    if path is None and (cwd / "factory.toml").exists():
        path = cwd / "factory.toml"
    global_path = _usable_global_config_path()
    global_layer = _global_layer(global_path) if global_path and global_path.exists() else {}
    typed = _merged(global_layer, {} if path is None else _layer(path))
    repo = typed.pop("repo", None) or _git_toplevel(cwd)
    configured_home = typed.pop("home", None)
    state_dir = typed.pop("state_dir", None) or _project_dir(repo, configured_home) / "state"
    worktree_root = typed.pop("worktree_root", None) or _project_dir(repo, configured_home) / "worktrees"
    plugin_dirs = _plugin_dirs(typed.get("plugins", ()), configured_home)
    stage_plugin_dirs = {
        stage: _plugin_dirs(names, configured_home) for stage, names in typed.get("stage_plugins", {}).items()
    }
    settings = replace(
        Settings(repo, state_dir, worktree_root), plugin_dirs=plugin_dirs, stage_plugin_dirs=stage_plugin_dirs, **typed
    )
    _check_project_subdir(settings.project_subdir)
    return settings


def _global_layer(path: Path) -> dict:
    try:
        return _layer(path, per_repo_allowed=False)
    except SettingsError as error:
        message = str(error)
        raise SettingsError(message if str(path) in message else f"{message} (in {path})") from None


_PER_REPO_KEYS = ("repo", "state_dir", "worktree_root", "watch_approvers")


def _layer(path: Path, per_repo_allowed: bool = True) -> dict:
    values = _read(path)
    if not per_repo_allowed and (misplaced := sorted(set(values) & set(_PER_REPO_KEYS))):
        raise SettingsError(f"{misplaced[0]} is per-repo and not allowed in the global config {path}")
    unknown = sorted(set(values) - set(_KNOWN))
    if unknown:
        raise SettingsError(f"unknown setting: {unknown[0]}")
    typed = {key: _typed(key, value) for key, value in values.items()}
    for key in _PATH_KEYS:
        if key in typed:
            typed[key] = path.parent / _expanded(key, typed[key])
    return typed


def _merged(base: dict, override: dict) -> dict:
    merged = {**base, **override}
    for table in ("stage_prompts", "stage_plugins"):
        if table in base and table in override:
            merged[table] = {**base[table], **override[table]}
    return merged


def _environment_home() -> Path | None:
    value = os.environ.get("FACTORY_HOME")
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise SettingsError("FACTORY_HOME must be an absolute path")
    return path


def global_config_path() -> Path:
    return (_environment_home() or _default_home()) / "factory.toml"


def _usable_global_config_path() -> Path | None:
    try:
        return global_config_path()
    except SettingsError:
        return None  # an unusable home is reported only where the home is needed


def _expanded(key: str, value: str) -> Path:
    path = Path(value)
    return path if key == "repo" else path.expanduser()


def _default_home() -> Path:
    if os.environ.get("HOME") == "":
        raise SettingsError("HOME is empty; set FACTORY_HOME or home in factory.toml")
    return Path.home() / ".harness"


def _harness_home(configured_home: Path | None) -> Path:
    return _environment_home() or configured_home or _default_home()


def harness_home() -> Path:
    path = global_config_path()
    configured = _global_layer(path).get("home") if path.exists() else None
    return _harness_home(configured)


def _project_dir(repo: Path, configured_home: Path | None) -> Path:
    home = _harness_home(configured_home)
    resolved = repo.resolve()
    key = resolved.name
    if key == "repo" and resolved.parent.parent == home.resolve():
        key = resolved.parent.name  # a clone made by `factory clone` lives at <home>/<project>/repo
    if not key:
        raise SettingsError("repo has no directory name to key the project by")
    return home / key


def _plugin_dirs(names: tuple[str, ...], configured_home: Path | None) -> tuple[Path, ...]:
    if not names:
        return ()
    shared = _harness_home(configured_home) / "_shared" / "plugins"
    for name in names:
        if not (shared / name).is_dir():
            raise SettingsError(f"plugin directory not found: {shared / name}")
        if feature := _runs_code(shared / name):
            raise SettingsError(f"plugin {name} is refused: it carries {feature}, which runs outside the tool rules")
    return tuple(shared / name for name in names)


def _read(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text())
    except FileNotFoundError:
        raise SettingsError(f"config file not found: {path}") from None
    except tomllib.TOMLDecodeError as error:
        raise SettingsError(f"invalid TOML in {path}: {error}") from None


def _typed(key: str, value):
    if key == "stage_prompts":
        if isinstance(value, dict) and set(value) <= set(_STAGES) and all(
            isinstance(text, str) and text for text in value.values()
        ):
            return dict(value)
    elif key == "stage_plugins":
        if isinstance(value, dict) and set(value) <= set(_STAGES) and all(
            isinstance(names, list) and names and all(isinstance(name, str) for name in names) and _plugin_names_ok(names)
            for names in value.values()
        ):
            return {stage: tuple(names) for stage, names in value.items()}
    elif key in _PATH_KEYS or key in _STRING_KEYS:
        if isinstance(value, str):
            return value
    elif key in _PROMPT_KEYS:
        if isinstance(value, str) and value:
            return value
    elif key in _FLOAT_KEYS:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    elif key in _INT_KEYS:
        if isinstance(value, int) and not isinstance(value, bool) and (value >= 0 or key == "max_turns"):
            return value
    elif isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        if key == "watch_approvers":
            if all(GITHUB_LOGIN.fullmatch(login) for login in value):
                return tuple(login.casefold() for login in value)
            raise SettingsError(f"wrong type for setting: {key}")
        if key != "plugins" or _plugin_names_ok(value):
            return tuple(value)
    raise SettingsError(f"wrong type for setting: {key}")


def _runs_code(plugin: Path) -> str | None:
    """Best effort: the plugin features known to run code (hooks, MCP servers); not a proof the plugin is safe."""
    if (plugin / "hooks").exists():
        return "hooks"
    if (plugin / ".mcp.json").exists():
        return ".mcp.json"
    manifest = plugin / ".claude-plugin" / "plugin.json"
    if manifest.is_file():
        try:
            declared = json.loads(manifest.read_text())
        except (OSError, ValueError):
            return "an unreadable plugin.json"
        for key in ("hooks", "mcpServers"):
            if isinstance(declared, dict) and key in declared:
                return key
    for markdown in (*sorted(plugin.glob("skills/*/SKILL.md")), *sorted(plugin.glob("agents/*.md"))):
        name = markdown.relative_to(plugin).as_posix()
        try:
            declares_hooks = _frontmatter_declares_hooks(markdown)
        except OSError:
            return f"an unreadable {name}"
        if declares_hooks:
            return f"frontmatter hooks in {name}"
    return None


def _frontmatter_declares_hooks(markdown: Path) -> bool:
    lines = markdown.read_text(errors="replace").splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    for line in lines[1:]:
        if line.strip() == "---":
            return False
        if line.startswith("hooks:"):
            return True
    return False


def _plugin_names_ok(names: list[str]) -> bool:
    return len(set(names)) == len(names) and all(
        name not in ("", ".", "..") and "/" not in name and "\\" not in name for name in names
    )


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
