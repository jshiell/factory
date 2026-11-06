import os
import subprocess
from pathlib import Path

from factory.command import RunnerConfig, build_command
from factory.step import RunResult
from factory.task import Task


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
            env={**os.environ, **env_overlay},
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        return RunResult(completed.stdout, completed.returncode)
