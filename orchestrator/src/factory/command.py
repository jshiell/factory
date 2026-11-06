from dataclasses import dataclass


@dataclass(frozen=True)
class RunnerConfig:
    model: str


def build_command(prompt: str, config: RunnerConfig) -> tuple[list[str], dict[str, str]]:
    argv = ["claude", "-p", prompt, "--output-format", "json", "--model", config.model]
    return argv, {}
