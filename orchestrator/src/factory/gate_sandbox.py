import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from factory.command import sandbox_prefix
from factory.process import STRIPPED_ENV


@dataclass(frozen=True)
class UvTooling:
    real_uv: str
    python_prefix: str
    uv_python_dir: str


@dataclass(frozen=True)
class GateSandbox:
    prefix: tuple[str, ...]
    argv: tuple[str, ...]
    env: dict[str, str]


def _without_shims(path: str) -> str:
    """mise shims need ~/.config/mise and ~/.local/state/mise, which the gate sandbox does not grant."""
    return os.pathsep.join(d for d in path.split(os.pathsep) if "shims" not in d)


def build_gate_sandbox(
    *, sandbox_executable: str, worktree: Path, project_dir: Path, cache_dir: Path, python_prefix: str,
    uv_python_dir: str, real_uv: str, base_env: dict[str, str], test_argv: tuple[str, ...],
) -> GateSandbox:
    """Own minimal grants for the gate (no claude profile): write the project dir and a private uv cache only."""
    prefix = sandbox_prefix(
        sandbox_executable, worktree, profile=None, allow=(str(project_dir), str(cache_dir)),
        read=(python_prefix, uv_python_dir),
    )
    argv = (real_uv, *test_argv[1:]) if test_argv and test_argv[0] == "uv" else tuple(test_argv)
    env = {k: v for k, v in base_env.items() if k not in STRIPPED_ENV}
    env.update(PATH=_without_shims(base_env.get("PATH", "")), UV_CACHE_DIR=str(cache_dir), UV_LINK_MODE="copy")
    return GateSandbox(tuple(prefix), argv, env)


def discover_uv_tooling(project_dir: Path, path: str | None = None) -> UvTooling:
    """Ask the real uv (trusted, run unconfined by the factory) where Python lives.

    `--no-config`: the project is agent-written, so its uv.toml, [tool.uv] and .python-version must not steer this call.
    """
    path = _without_shims(path if path is not None else os.environ.get("PATH", ""))
    real_uv = shutil.which("uv", path=path) or "uv"
    env = {**os.environ, "PATH": path}

    def ask(*args: str) -> str:
        return subprocess.run([real_uv, "--no-config", *args], cwd=project_dir, env=env, capture_output=True, text=True,
                              check=True, stdin=subprocess.DEVNULL).stdout.strip()

    python = Path(ask("python", "find")).resolve()
    return UvTooling(real_uv, str(python.parent.parent), ask("python", "dir"))
