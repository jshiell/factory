import os
import signal
import subprocess
import tempfile
from typing import NamedTuple

STRIPPED_ENV = ("VIRTUAL_ENV", "GIT_DIR", "GIT_WORK_TREE", "ANTHROPIC_API_KEY")


def _kill(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _descendants(pid: int) -> list[int]:
    listed = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True)
    children = [int(line) for line in listed.stdout.split()]
    return children + [d for child in children for d in _descendants(child)]


def kill_tree(root_pid: int) -> None:
    """Kill every descendant (even those that left the process group), then the root's process group."""
    for pid in _descendants(root_pid):
        _kill(pid)
    try:
        os.killpg(root_pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class ExitResult(NamedTuple):
    exit_code: int | None
    timed_out: bool
    tail: str | None = None


def run_to_exit(
    argv: list[str], cwd, env: dict[str, str] | None, timeout_s: float, capture_limit: int | None = None
) -> ExitResult:
    """Run `argv` in its own session; on a timeout or any interruption kill the whole tree.

    With `capture_limit`, stdout and stderr go to one unlinked temp file and the last `capture_limit`
    characters come back as `tail` (read by seeking from the end: the command may print without limit)."""
    with tempfile.TemporaryFile() as output:
        sink = subprocess.DEVNULL if capture_limit is None else output
        process = subprocess.Popen(
            argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=sink, stderr=sink, start_new_session=True,
        )
        timed_out = False
        try:
            process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_tree(process.pid)
            process.wait()
        except BaseException:
            kill_tree(process.pid)
            process.wait()
            raise
        tail = None if capture_limit is None else _tail(output, capture_limit)
    return ExitResult(None if timed_out else process.returncode, timed_out, tail)


def _tail(output, limit: int) -> str:
    size = output.seek(0, os.SEEK_END)
    output.seek(max(0, size - 4 * limit))  # a character is at most 4 bytes
    return output.read().decode("utf-8", errors="replace")[-limit:]
