"""THROWAWAY helpers for the spike scripts: the two forwarders retired from factory.command in plan 64."""
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parent.parent / "orchestrator" / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from factory.command import RunnerConfig  # noqa: E402
from factory.harness import SANDBOX_PROFILE, ClaudeHarness  # noqa: E402
from factory.sandbox import NonoSandbox, SandboxPolicy  # noqa: E402


def build_command(
    prompt: str, config: RunnerConfig, executable: str = "claude", add_dirs: tuple[str, ...] = ()
) -> tuple[list[str], dict[str, str]]:
    invocation = ClaudeHarness().build(prompt, config, executable, add_dirs)
    return invocation.argv, invocation.env


def sandbox_prefix(
    executable: str, worktree: str | Path, profile: str | None = SANDBOX_PROFILE,
    allow: tuple[str, ...] = (), read: tuple[str, ...] = (),
) -> list[str]:
    """nono prefix; without a profile `--allow-cwd` is read-only, so write access is granted via `allow`."""
    return NonoSandbox(executable).wrap(SandboxPolicy(worktree, write=allow, read=read, profile=profile))
