import json
import os
from dataclasses import dataclass
from typing import Protocol

from factory.command import MAX_PROMPT_BYTES, PromptTooLarge, RunnerConfig


@dataclass(frozen=True)
class Invocation:
    argv: list[str]
    env: dict[str, str]


class Harness(Protocol):
    """How one agent CLI is started. The runner owns launching, timeout, sandboxing and records."""

    def build(
        self, prompt: str, config: RunnerConfig, executable: str, add_dirs: tuple[str, ...] = ()
    ) -> Invocation: ...


class ClaudeHarness:
    def build(
        self, prompt: str, config: RunnerConfig, executable: str, add_dirs: tuple[str, ...] = ()
    ) -> Invocation:
        if prompt.startswith("-"):
            raise ValueError("prompt must not start with '-'")
        size = len(os.fsencode(prompt))
        if size > MAX_PROMPT_BYTES:
            raise PromptTooLarge(f"prompt is {size} bytes, the limit is {MAX_PROMPT_BYTES}")
        argv = [executable, "-p", prompt, "--output-format", "json", "--model", config.model]
        argv += ["--setting-sources", ""]
        if config.budget_usd is not None:
            argv += ["--max-budget-usd", str(config.budget_usd)]
        if config.permission_mode is not None:
            argv += ["--permission-mode", config.permission_mode]
        if config.allowed_tools:
            argv += ["--allowedTools", " ".join(config.allowed_tools)]
        if config.json_schema is not None:
            argv += ["--json-schema", json.dumps(config.json_schema)]
        if config.append_system_prompt is not None:
            argv += ["--append-system-prompt", config.append_system_prompt]
        if config.max_turns is not None:
            argv += ["--max-turns", str(config.max_turns)]
        if config.disallowed_tools:
            argv += ["--disallowedTools", " ".join(config.disallowed_tools)]
        for directory in add_dirs:
            argv += ["--add-dir", directory]
        for directory in config.plugin_dirs:
            argv += ["--plugin-dir", directory]
        return Invocation(argv, {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"})
