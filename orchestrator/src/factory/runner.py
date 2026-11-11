import os
import subprocess
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path

from factory.command import RunnerConfig, build_command, sandbox_prefix
from factory.process import STRIPPED_ENV, kill_tree
from factory.process_registry import Tracker
from factory.step import RunResult
from factory.task import Task


class SubprocessRunner:
    def __init__(self, executable: str, project_subdir: str, timeout_s: float, sandbox_executable: str = "nono", records=None):
        """`records`: a records object or a state directory; each agent process is recorded there for the reaper."""
        self._executable = executable
        self._project_subdir = project_subdir
        self._timeout_s = timeout_s
        self._sandbox_executable = sandbox_executable
        self._records = records

    def run(self, task: Task, config: RunnerConfig) -> RunResult:
        worktree = str(Path(task.worktree).resolve())
        add_dirs = (worktree,) if config.confine else ()
        argv, env_overlay = build_command(task.prompt, config, executable=self._executable, add_dirs=add_dirs)
        if config.confine:
            argv = sandbox_prefix(self._sandbox_executable, worktree) + argv
        cwd = Path(task.worktree) / self._project_subdir
        tracker = Tracker(self._records, task.id, task.state.value) if self._records else None
        with tempfile.TemporaryFile("w+") as stdout_file:
            launched_after = time.time()
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
            with (tracker.launched(process.pid, launched_after, time.time()) if tracker else nullcontext()):
                try:
                    process.wait(timeout=self._timeout_s)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    kill_tree(process.pid)
                    process.wait()
                except BaseException:
                    kill_tree(process.pid)
                    process.wait()
                    raise
                else:
                    kill_tree(process.pid)  # whatever the agent left running in its session
            stdout_file.seek(0)
            return RunResult(stdout_file.read(), process.returncode, timed_out)

    @staticmethod
    def _child_env(overlay: dict[str, str]) -> dict[str, str]:
        inherited = {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV}
        return {**inherited, **overlay}
