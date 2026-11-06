from dataclasses import dataclass


@dataclass(frozen=True)
class RunnerConfig:
    model: str


def build_command(prompt: str, config: RunnerConfig) -> tuple[list[str], dict[str, str]]:
    argv = ["claude", "-p", prompt, "--output-format", "json", "--model", config.model]
    argv += ["--setting-sources", ""]
    return argv, {"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"}
