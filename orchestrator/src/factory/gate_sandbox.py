import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from factory.process import STRIPPED_ENV
from factory.sandbox import NonoSandbox, Sandbox, SandboxPolicy, without_private


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


def _uv_read_dirs(real_uv: str, home: str) -> tuple[str, ...]:
    """The directory holding uv, which nono needs readable to exec it.

    Empty for a bare or relative name, or for a directory that is `/` or holds home (agent tests must not read it)."""
    if not os.path.isabs(real_uv):
        return ()
    directory = str(Path(real_uv).resolve().parent)
    return without_private((directory,), (home,))


def build_gate_sandbox(
    *, worktree: Path, project_dir: Path, cache_dir: Path, python_prefix: str, uv_python_dir: str, real_uv: str,
    base_env: dict[str, str], test_argv: tuple[str, ...], sandbox_executable: str = "nono", sandbox: Sandbox | None = None,
) -> GateSandbox:
    """Own minimal grants for the gate (no claude profile): write the project dir and a private uv cache only.
    `sandbox` wraps the run (default: nono at `sandbox_executable`; `sandbox` wins if both are given)."""
    policy = SandboxPolicy(
        worktree, write=(str(project_dir), str(cache_dir)),
        read=(python_prefix, uv_python_dir, *_uv_read_dirs(real_uv, base_env.get("HOME", "/"))),
    )
    prefix = (sandbox or NonoSandbox(sandbox_executable)).wrap(policy)
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
