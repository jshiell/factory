from dataclasses import dataclass
from pathlib import Path

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


def contains(directory: str, inner: str) -> bool:
    return Path(inner).resolve().is_relative_to(Path(directory).resolve())
