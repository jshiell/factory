import os
import signal
import subprocess
import tempfile
from pathlib import Path

from factory.command import RunnerConfig, build_command, sandbox_prefix
from factory.step import RunResult
from factory.task import Task

_STRIPPED_ENV = ("VIRTUAL_ENV", "GIT_DIR", "GIT_WORK_TREE", "ANTHROPIC_API_KEY")


def _kill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class SubprocessRunner:
    def __init__(self, executable: str, project_subdir: str, timeout_s: float, sandbox_executable: str = "nono"):
        self._executable = executable
        self._project_subdir = project_subdir
        self._timeout_s = timeout_s
        self._sandbox_executable = sandbox_executable

    def run(self, task: Task, config: RunnerConfig) -> RunResult:
        worktree = str(Path(task.worktree).resolve())
        add_dirs = (worktree,) if config.confine else ()
        argv, env_overlay = build_command(task.prompt, config, executable=self._executable, add_dirs=add_dirs)
        if config.confine:
            argv = sandbox_prefix(self._sandbox_executable, worktree) + argv
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
                self._kill_tree(process.pid)
                process.wait()
            except BaseException:
                self._kill_tree(process.pid)
                process.wait()
                raise
            stdout_file.seek(0)
            return RunResult(stdout_file.read(), process.returncode, timed_out)

    @staticmethod
    def _descendants(pid: int) -> list[int]:
        listed = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True)
        children = [int(line) for line in listed.stdout.split()]
        return children + [d for child in children for d in SubprocessRunner._descendants(child)]

    @staticmethod
    def _kill_tree(root_pid: int) -> None:
        for pid in SubprocessRunner._descendants(root_pid):
            _kill(pid)
        try:
            os.killpg(root_pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    @staticmethod
    def _child_env(overlay: dict[str, str]) -> dict[str, str]:
        inherited = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV}
        return {**inherited, **overlay}
