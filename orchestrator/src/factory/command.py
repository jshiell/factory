import json
from dataclasses import dataclass


@dataclass(frozen=True)
class RunnerConfig:
    model: str
    budget_usd: float | None = None
    permission_mode: str | None = None
    allowed_tools: tuple[str, ...] = ()
    json_schema: dict | None = None


def build_command(prompt: str, config: RunnerConfig) -> tuple[list[str], dict[str, str]]:
    argv = ["claude", "-p", prompt, "--output-format", "json", "--model", config.model]
    argv += ["--setting-sources", ""]
    if config.budget_usd is not None:
        argv += ["--max-budget-usd", str(config.budget_usd)]
    if config.permission_mode is not None:
        argv += ["--permission-mode", config.permission_mode]
    if config.allowed_tools:
        argv += ["--allowedTools", " ".join(config.allowed_tools)]
    if config.json_schema is not None:
        argv += ["--json-schema", json.dumps(config.json_schema)]
    return argv, {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"}
