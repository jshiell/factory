import json
import os
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from factory.classify import Outcome, OutcomeKind
from factory.command import (
    MAX_PROMPT_BYTES,
    SANDBOX_PROFILE,
    PromptTooLarge,
    RunnerConfig,
)


class Capability(Enum):
    """What the stages rely on a harness to honour. Every stage needs all of them today: the plan, review and
    audit stages need `json_schema`; the plan stage (grilling is chosen per task) needs `plugins`; every stage
    has a budget; the implementer's shell ban and the read-only stages are tool rules."""

    JSON_SCHEMA = "json_schema"
    PLUGINS = "plugins"
    BUDGET = "budget"
    TOOL_RULES = "tool_rules"


def missing_capabilities(harness: Harness) -> list[Capability]:
    return [capability for capability in Capability if capability not in harness.capabilities]


@dataclass(frozen=True)
class Invocation:
    argv: list[str]
    env: dict[str, str]


class Harness(Protocol):
    """How one agent CLI is started. The runner owns launching, timeout, sandboxing and records."""

    sandbox_profile: str | None
    capabilities: frozenset[Capability]

    def build(
        self, prompt: str, config: RunnerConfig, executable: str, add_dirs: tuple[str, ...] = ()
    ) -> Invocation: ...

    def parse(self, stdout: str, exit_code: int) -> Outcome: ...


class ClaudeHarness:
    sandbox_profile = SANDBOX_PROFILE
    capabilities = frozenset(Capability)

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

    def parse(self, stdout: str, exit_code: int) -> Outcome:
        payload = self._parse_result(stdout)
        if payload is None:
            return Outcome(OutcomeKind.UNPARSEABLE, exit_code)
        if payload.get("subtype") == "error_max_budget_usd":
            kind = OutcomeKind.BUDGET_EXHAUSTED
        elif payload.get("is_error") or payload.get("subtype") != "success":
            kind = OutcomeKind.ERROR
        elif payload.get("permission_denials"):
            kind = OutcomeKind.SUCCESS_WITH_DENIALS
        else:
            kind = OutcomeKind.SUCCESS
        return Outcome(
            kind,
            exit_code,
            cost_usd=payload.get("total_cost_usd", 0.0),
            subtype=payload.get("subtype"),
            errors=tuple(payload.get("errors", ())),
            denials=tuple(payload.get("permission_denials", ())),
            structured_output=payload.get("structured_output"),
            result=payload.get("result"),
        )

    @staticmethod
    def _parse_result(stdout: str) -> dict | None:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict) or payload.get("type") != "result":
            return None
        return payload


DEFAULT_HARNESS = "claude"
HARNESSES: dict[str, Harness] = {DEFAULT_HARNESS: ClaudeHarness()}


def harness_named(name: str) -> Harness:
    return HARNESSES[name]
