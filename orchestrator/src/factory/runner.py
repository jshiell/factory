import os
import signal
import subprocess
import tempfile
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
        with tempfile.TemporaryFile("w+") as stdout_file:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=self._child_env(env_overlay),
                stdout=stdout_file,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                text=True,
                start_new_session=True,
            )
            timed_out = False
            try:
                process.wait(timeout=self._timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            stdout_file.seek(0)
            return RunResult(stdout_file.read(), process.returncode, timed_out)

    @staticmethod
    def _child_env(overlay: dict[str, str]) -> dict[str, str]:
        inherited = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV}
        return {**inherited, **overlay}
