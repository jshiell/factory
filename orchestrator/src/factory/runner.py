import os
import shutil
import subprocess
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path

from factory.command import RunnerConfig
from factory.harness import ClaudeHarness, Harness
from factory.process import STRIPPED_ENV, kill_tree, tail
from factory.process_registry import Tracker
from factory.sandbox import NonoSandbox, Sandbox, SandboxPolicy, without_private
from factory.step import TAIL_CHARS, RunResult
from factory.task import Task


def _executable_directory(executable: str, path: str | None, private_dirs: tuple[str, ...]) -> tuple[str, ...]:
    """The directories the sandbox must be allowed to read for `executable` to start: the one it is found in and,
    for a symlinked file, the one it resolves into. Empty if it cannot be found; each is dropped if reading it
    would expose a private directory."""
    found = shutil.which(executable, path=path)
    if not found:
        return ()
    directory = os.path.dirname(os.path.abspath(found))
    resolved = str(Path(found).resolve().parent)
    candidates = (directory,) if resolved == str(Path(directory).resolve()) else (directory, resolved)
    return without_private(candidates, private_dirs)


class SubprocessRunner:
    def __init__(self, executable: str, project_subdir: str, timeout_s: float, sandbox_executable: str = "nono", records=None, private_dirs: tuple[str, ...] = (), harness: Harness | None = None, sandbox: Sandbox | None = None):
        """`records`: a records object or a state directory; each agent process is recorded there for the reaper.
        `private_dirs`: directories the sandbox must never be granted read access to, even indirectly.
        `harness`: how the agent CLI is started (default: Claude).
        `sandbox`: how a confined run is wrapped (default: nono at `sandbox_executable`; `sandbox` wins if both are given)."""
        self._harness = harness or ClaudeHarness()
        self._sandbox = sandbox or NonoSandbox(sandbox_executable)
        self._private_dirs = private_dirs
        self._executable = executable
        self._project_subdir = project_subdir
        self._timeout_s = timeout_s
        self._records = records

    def run(self, task: Task, config: RunnerConfig) -> RunResult:
        worktree = str(Path(task.worktree).resolve())
        add_dirs = (worktree,) if config.confine else ()
        invocation = self._harness.build(task.prompt, config, self._executable, add_dirs)
        argv = invocation.argv
        child_env = self._child_env(invocation.env)
        if config.confine:
            private_dirs = (*self._private_dirs, *filter(None, [child_env.get("HOME")]))
            executable_grant = _executable_directory(self._executable, child_env.get("PATH"), private_dirs)
            policy = SandboxPolicy(
                worktree, read=(*config.plugin_dirs, *executable_grant), profile=self._harness.sandbox_profile,
            )
            argv = self._sandbox.wrap(policy) + argv
        cwd = Path(task.worktree) / self._project_subdir
        tracker = Tracker(self._records, task.id, task.state.value) if self._records else None
        with tempfile.TemporaryFile("w+") as stdout_file, tempfile.TemporaryFile() as stderr_file:
            launched_after = time.time()
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=child_env,
                stdout=stdout_file,
                stderr=stderr_file,
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
            return RunResult(
                stdout_file.read(), process.returncode, timed_out, tail(stderr_file, TAIL_CHARS),
                parse=self._harness.parse,
            )

    @staticmethod
    def _child_env(overlay: dict[str, str]) -> dict[str, str]:
        inherited = {k: v for k, v in os.environ.items() if k not in STRIPPED_ENV}
        return {**inherited, **overlay}
