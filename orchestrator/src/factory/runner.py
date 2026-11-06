import os
import subprocess
from pathlib import Path

from factory.command import RunnerConfig, build_command
from factory.step import RunResult
from factory.task import Task

_STRIPPED_ENV = ("VIRTUAL_ENV", "GIT_DIR", "GIT_WORK_TREE", "ANTHROPIC_API_KEY")


class SubprocessRunner:
    def __init__(self, executable: str, project_subdir: str, timeout_s: float):
        self._executable = executable
        self._project_subdir = project_subdir
        self._timeout_s = timeout_s

    def run(self, task: Task, config: RunnerConfig) -> RunResult:
        argv, env_overlay = build_command(task.prompt, config, executable=self._executable)
        cwd = Path(task.worktree) / self._project_subdir
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=self._child_env(env_overlay),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        return RunResult(completed.stdout, completed.returncode)

    @staticmethod
    def _child_env(overlay: dict[str, str]) -> dict[str, str]:
        inherited = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV}
        return {**inherited, **overlay}
