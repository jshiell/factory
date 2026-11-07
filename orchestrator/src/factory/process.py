import os
import signal
import subprocess

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


def run_to_exit(argv: list[str], cwd, env: dict[str, str] | None, timeout_s: float) -> tuple[int | None, bool]:
    """Run `argv` silently in its own session; on a timeout or any interruption kill the whole tree.

    Returns (exit code, timed_out)."""
    process = subprocess.Popen(
        argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        process.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        kill_tree(process.pid)
        process.wait()
        return None, True
    except BaseException:
        kill_tree(process.pid)
        process.wait()
        raise
    return process.returncode, False
