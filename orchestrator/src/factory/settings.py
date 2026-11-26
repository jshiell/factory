import ipaddress
import json
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath

from factory.gate import in_factory_dir
from factory.harness import DEFAULT_HARNESS, HARNESSES
from factory.sandbox import DEFAULT_SANDBOX, SANDBOXES


class SettingsError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    repo: Path
    state_dir: Path
    worktree_root: Path
    project_subdir: str = "."
    test_argv: tuple[str, ...] = ("uv", "run", "pytest", "-q")
    cap_usd: float = 5.0
    gate_timeout_s: float = 120.0
    runner_timeout_s: float = 300.0
    claude_executable: str = "claude"
    harness: str = DEFAULT_HARNESS
    sandbox: str = DEFAULT_SANDBOX
    sandbox_executable: str | None = None
    model: str = "sonnet"
    budget_usd: float = 0.5
    allowed_tools: tuple[str, ...] = ()
    max_turns: int = 30
    fix_rounds: int = 2
    server_port: int = 8765
    server_host: str = "127.0.0.1"
    server_allowed_hosts: tuple[str, ...] = ()
    grill: bool = True
    grill_max_rounds: int = 3
    append_system_prompt: str | None = None
    stage_prompts: dict[str, str] = field(default_factory=dict)
    plugins: tuple[str, ...] = ()
    plugin_dirs: tuple[Path, ...] = ()
    stage_plugins: dict[str, tuple[str, ...]] = field(default_factory=dict)
    stage_plugin_dirs: dict[str, tuple[Path, ...]] = field(default_factory=dict)
    watch_approvers: tuple[str, ...] = ()
    watch_merge: bool = False
    watch_retry: bool = False
    gate_allow_dependency_changes: bool = False
    main_branch: str | None = None
    warnings: tuple[str, ...] = ()
    home: Path | None = None

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

    @property
    def server_dir(self) -> Path:
        if self.home is None:
            raise SettingsError("no factory home is known, so there is no server directory")
        return self.home / "_server"

    @property
    def token_path(self) -> Path:
        return self.server_dir / "token"

    @property
    def private_dirs(self) -> tuple[str, ...]:
        """Directories the sandbox must never be granted, even indirectly."""
        server = () if self.home is None else (str(self.server_dir),)
        return (str(self.state_dir), *server)


@dataclass(frozen=True)
class GlobalSettings:
    """What the one server needs before any repo is loaded."""

    home: Path
    server_port: int = 8765
    server_host: str = "127.0.0.1"
    server_allowed_hosts: tuple[str, ...] = ()


BIND_HOSTS = ("127.0.0.1", "0.0.0.0")
_ALWAYS_ALLOWED_HOSTS = ("127.0.0.1", "localhost", "0.0.0.0")
_HOST_NAME = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)*")
_NUMERIC_HOST = re.compile(r"[0-9.]+")
_PATH_KEYS = ("repo", "state_dir", "worktree_root", "home")
_STRING_KEYS = ("project_subdir", "claude_executable", "sandbox_executable", "model", "main_branch", "harness", "sandbox", "server_host")
_PROMPT_KEYS = ("append_system_prompt",)
_STAGES = ("plan", "review", "implement", "audit")
_FLOAT_KEYS = ("cap_usd", "gate_timeout_s", "runner_timeout_s", "budget_usd")
_STRING_LIST_KEYS = ("test_argv", "allowed_tools", "plugins", "watch_approvers", "server_allowed_hosts")
_INT_KEYS = ("max_turns", "fix_rounds", "server_port", "grill_max_rounds")
_BOOL_KEYS = ("grill", "watch_merge", "watch_retry", "gate_allow_dependency_changes")
GITHUB_LOGIN = re.compile(r"(?!.{40})[A-Za-z0-9]+(-[A-Za-z0-9]+)*")
_KNOWN =_PATH_KEYS + _STRING_KEYS + _PROMPT_KEYS + ("stage_prompts", "stage_plugins") + _FLOAT_KEYS + _STRING_LIST_KEYS + _INT_KEYS + _BOOL_KEYS


PROJECT_CONFIG = Path(".factory") / "factory.toml"


def load_settings(path: Path | None, cwd: Path) -> Settings:
    toplevel = _toplevel_or_none(cwd)
    if toplevel is not None:
        _check_no_old_layout(toplevel)
        if path is None and (toplevel / PROJECT_CONFIG).exists():
            path = toplevel / PROJECT_CONFIG
    global_path = _usable_global_config_path()
    global_layer = _global_layer(global_path) if global_path and global_path.exists() else {}
    typed = _merged(global_layer, {} if path is None else _layer(path))
    repo = typed.pop("repo", None) or _git_toplevel(cwd)
    if path is not None:
        _check_config_outside_editable_area(path, {_toplevel_or_none(repo), toplevel} - {None})
    configured_home = typed.pop("home", None)
    state_dir = typed.pop("state_dir", None) or _project_dir(repo, configured_home) / "state"
    worktree_root = typed.pop("worktree_root", None) or _project_dir(repo, configured_home) / "worktrees"
    if toplevel is not None:
        _check_no_in_repo_state(toplevel / ".factory", state_dir)
    plugin_dirs = _plugin_dirs(typed.get("plugins", ()), configured_home)
    stage_plugin_dirs = {
        stage: _plugin_dirs(names, configured_home) for stage, names in typed.get("stage_plugins", {}).items()
    }
    settings = replace(
        Settings(repo, state_dir, worktree_root), plugin_dirs=plugin_dirs, stage_plugin_dirs=stage_plugin_dirs,
        warnings=_project_subdir_warnings(repo, typed), home=_usable_home(configured_home), **typed,
    )
    _check_project_subdir(settings.project_subdir)
    if settings.harness not in HARNESSES:
        raise SettingsError(f"harness {settings.harness!r} is unknown; known: {', '.join(sorted(HARNESSES))}")
    if settings.sandbox not in SANDBOXES:
        raise SettingsError(f"sandbox {settings.sandbox!r} is unknown; known: {', '.join(sorted(SANDBOXES))}")
    _check_server(settings.server_host, settings.server_allowed_hosts)
    return settings


def load_global_settings() -> GlobalSettings:
    path = global_config_path()
    layer = _global_layer(path) if path.exists() else {}
    settings = GlobalSettings(
        home=_harness_home(layer.get("home")),
        **{key: layer[key] for key in ("server_port", "server_host", "server_allowed_hosts") if key in layer},
    )
    _check_server(settings.server_host, settings.server_allowed_hosts)
    return settings


def _check_server(host: str, allowed_hosts: tuple[str, ...]) -> None:
    if host not in BIND_HOSTS:
        raise SettingsError(f"server_host {host!r} is unknown; known: {', '.join(BIND_HOSTS)}")
    _check_allowed_hosts(allowed_hosts)


def _project_subdir_warnings(repo: Path, typed: dict) -> tuple[str, ...]:
    # The default moved from target to the repo root; a repo laid out for the old default should say which it means.
    if "project_subdir" in typed or not (repo / "target").is_dir():
        return ()
    return (
        'project_subdir is unset, so tasks now work at the repo root ("."), but this repo has target/, '
        'the old default: set project_subdir = "target" in .factory/factory.toml to keep working there, '
        'or project_subdir = "." to silence this',
    )


def _check_no_old_layout(toplevel: Path) -> None:
    # A root factory.toml is editable by tasks; only `.factory/` is protected from them.
    if (toplevel / "factory.toml").exists():
        raise SettingsError(
            f"{toplevel / 'factory.toml'} is no longer read: the project config lives in .factory/factory.toml. "
            "Move it with `git mv factory.toml .factory/factory.toml` (or plain `mv` if .factory/ is gitignored)"
        )


def _check_config_outside_editable_area(path: Path, repos: set[Path]) -> None:
    # Tasks may edit the whole repo except `.factory/`, so a config anywhere else in it could be rewritten by a task.
    resolved = path.resolve()
    for repo in repos:
        root = repo.resolve()
        if resolved.is_relative_to(root) and not in_factory_dir(resolved.relative_to(root)):
            raise SettingsError(f"config {path} is inside the repo {repo}; keep it in .factory/ (tasks can edit the rest)")


def _check_no_in_repo_state(factory_dir: Path, state_dir: Path) -> None:
    if state_dir.resolve() == factory_dir.resolve():
        return  # the old state is configured on purpose, as `migrate` needs
    for name in ("factory.db", "token"):
        if (factory_dir / name).exists():
            raise SettingsError(
                f"{factory_dir / name} is old in-repo state, which is no longer read: "
                f"move the state files in {factory_dir} (factory.db, token, tasks, ledger) to {state_dir}"
            )


def _check_allowed_hosts(hosts: tuple[str, ...]) -> None:
    for position, host in enumerate(hosts):
        problem = _allowed_host_problem(host) or ("is a duplicate" if host in hosts[:position] else None)
        if problem:
            raise SettingsError(f"server_allowed_hosts {host!r} is invalid: {problem}")


def _allowed_host_problem(host: str) -> str | None:
    if host in _ALWAYS_ALLOWED_HOSTS:
        return "this name is already accepted or can never be a Host"
    if not _HOST_NAME.fullmatch(host):
        return "use lowercase letters, digits and hyphens separated by dots, or an IPv4 address (no port, scheme, wildcard, trailing dot or IPv6)"
    if _NUMERIC_HOST.fullmatch(host) and not _is_ipv4(host):
        return "a numeric name must be a full IPv4 address of four octets, 0-255, without leading zeros"
    return None


def _is_ipv4(host: str) -> bool:
    try:
        ipaddress.IPv4Address(host)
    except ValueError:
        return False
    return True


def _global_layer(path: Path) -> dict:
    try:
        return _layer(path, per_repo_allowed=False)
    except SettingsError as error:
        message = str(error)
        raise SettingsError(message if str(path) in message else f"{message} (in {path})") from None


_PER_REPO_KEYS = (
    "repo", "state_dir", "worktree_root", "watch_approvers", "watch_merge", "watch_retry", "main_branch",
    "gate_allow_dependency_changes",  # a standing opt-out of a security check: never global
)


_GLOBAL_ONLY_KEYS = ("home", "server_port", "server_host", "server_allowed_hosts")  # one server serves every repo


def _layer(path: Path, per_repo_allowed: bool = True) -> dict:
    values = _read(path)
    if per_repo_allowed and (misplaced := sorted(set(values) & set(_GLOBAL_ONLY_KEYS))):
        raise SettingsError(f"{misplaced[0]} is global-only and not allowed in the repo config {path}")
    if not per_repo_allowed and (misplaced := sorted(set(values) & set(_PER_REPO_KEYS))):
        raise SettingsError(f"{misplaced[0]} is per-repo and not allowed in the global config {path}")
    if "gate_allow_outside" in values:
        raise SettingsError(
            f"gate_allow_outside in {path} is no longer read: tasks may now edit the whole repo except .factory/, "
            "so there is nothing outside to allow; remove it"
        )
    unknown = sorted(set(values) - set(_KNOWN))
    if unknown:
        raise SettingsError(f"unknown setting: {unknown[0]}")
    typed = {key: _typed(key, value) for key, value in values.items()}
    base = _relative_base(path)
    for key in _PATH_KEYS:
        if key in typed:
            typed[key] = base / _expanded(key, typed[key])
    return typed


def _relative_base(path: Path) -> Path:
    # `.factory/factory.toml` speaks for the repo it sits in, so its paths are relative to the repo root.
    return path.parent.parent if path.parent.name == ".factory" else path.parent


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


def _usable_home(configured_home: Path | None) -> Path | None:
    try:
        return _harness_home(configured_home)
    except SettingsError:
        return None  # an unusable home is reported only where the home is needed


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
        if isinstance(value, str) and (value or key != "main_branch"):
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
    elif key in _BOOL_KEYS:
        if isinstance(value, bool):
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
    path = PurePosixPath(subdir)
    parts = path.parts
    if not subdir:
        raise SettingsError('project_subdir must not be empty; write "." for the repo root')
    if path.is_absolute() or ".." in parts:
        raise SettingsError("project_subdir must be a relative path without '..'")
    if in_factory_dir(path):
        raise SettingsError("project_subdir may not be .factory or inside it; the factory's own files live there")


def _git_toplevel(cwd: Path) -> Path:
    toplevel = _toplevel_or_none(cwd)
    if toplevel is None:
        raise SettingsError("repo not set and the current directory is not in a git repository")
    return toplevel


def _toplevel_or_none(cwd: Path) -> Path | None:
    if not cwd.is_dir():
        return None
    completed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=cwd, capture_output=True, text=True,
        stdin=subprocess.DEVNULL,
    )
    return Path(completed.stdout.strip()) if completed.returncode == 0 else None
