from dataclasses import dataclass
from pathlib import Path

SANDBOX_PROFILE = "nolabs-ai/claude"
# Linux caps one argv string at 131072 bytes including the NUL (measured with /usr/bin/true: 131071 runs, 131072 is E2BIG).
MAX_PROMPT_BYTES = 131_071


class PromptTooLarge(ValueError):
    """The prompt cannot be passed as one argv string."""


@dataclass(frozen=True)
class RunnerConfig:
    model: str
    budget_usd: float | None = None
    permission_mode: str | None = None
    allowed_tools: tuple[str, ...] = ()
    json_schema: dict | None = None
    append_system_prompt: str | None = None
    max_turns: int | None = None
    disallowed_tools: tuple[str, ...] = ()
    confine: bool = False
    plugin_dirs: tuple[str, ...] = ()


def build_command(
    prompt: str, config: RunnerConfig, executable: str = "claude", add_dirs: tuple[str, ...] = ()
) -> tuple[list[str], dict[str, str]]:
    from factory.harness import ClaudeHarness  # harness.py imports this module

    invocation = ClaudeHarness().build(prompt, config, executable, add_dirs)
    return invocation.argv, invocation.env


def contains(directory: str, inner: str) -> bool:
    return Path(inner).resolve().is_relative_to(Path(directory).resolve())


def sandbox_prefix(
    executable: str, worktree: str | Path, profile: str | None = SANDBOX_PROFILE,
    allow: tuple[str, ...] = (), read: tuple[str, ...] = (),
) -> list[str]:
    """nono prefix; without a profile `--allow-cwd` is read-only, so write access is granted via `allow`."""
    prefix = [executable, "run", "-s"] + (["--profile", profile] if profile else [])
    prefix += ["--allow-cwd", "--read", str(Path(worktree).resolve())]
    for directory in allow:
        prefix += ["--allow", directory]
    for directory in read:
        prefix += ["--read", directory]
    return [*prefix, "--"]
