import os
import shutil
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


def _contains(directory: str, inner: str) -> bool:
    return Path(inner).resolve().is_relative_to(Path(directory).resolve())


def _executable_directory(executable: str, path: str | None, private_dirs: tuple[str, ...]) -> tuple[str, ...]:
    """The directory the sandbox must be allowed to read for `executable` to start.
    None if it cannot be found, or if reading it would expose a private directory."""
    found = shutil.which(executable, path=path)
    if not found:
        return ()
    directory = os.path.dirname(os.path.abspath(found))
    return () if any(_contains(directory, private) for private in private_dirs) else (directory,)


class SubprocessRunner:
    def __init__(self, executable: str, project_subdir: str, timeout_s: float, sandbox_executable: str = "nono", records=None, private_dirs: tuple[str, ...] = ()):
        """`records`: a records object or a state directory; each agent process is recorded there for the reaper.
        `private_dirs`: directories the sandbox must never be granted read access to, even indirectly."""
        self._private_dirs = private_dirs
        self._executable = executable
        self._project_subdir = project_subdir
        self._timeout_s = timeout_s
        self._sandbox_executable = sandbox_executable
        self._records = records

    def run(self, task: Task, config: RunnerConfig) -> RunResult:
        worktree = str(Path(task.worktree).resolve())
        add_dirs = (worktree,) if config.confine else ()
        argv, env_overlay = build_command(task.prompt, config, executable=self._executable, add_dirs=add_dirs)
        child_env = self._child_env(env_overlay)
        if config.confine:
            executable_grant = _executable_directory(self._executable, child_env.get("PATH"), self._private_dirs)
            argv = sandbox_prefix(self._sandbox_executable, worktree, read=(*config.plugin_dirs, *executable_grant)) + argv
        cwd = Path(task.worktree) / self._project_subdir
        tracker = Tracker(self._records, task.id, task.state.value) if self._records else None
        with tempfile.TemporaryFile("w+") as stdout_file:
            launched_after = time.time()
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=child_env,
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
