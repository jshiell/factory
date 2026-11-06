import os
import signal
import stat
import time
from pathlib import Path

import pytest

from factory.command import RunnerConfig
from factory.runner import SubprocessRunner
from factory.task import Task

CONFIG = RunnerConfig(model="sonnet", budget_usd=0.5)


def fake_claude(tmp_path: Path, body: str) -> str:
    script = tmp_path / "fake-claude"
    script.write_text("#!/bin/sh\n" + body + "\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


@pytest.fixture
def task(tmp_path):
    (tmp_path / "wt" / "proj").mkdir(parents=True)
    return Task(id="t1", prompt="do it", worktree=str(tmp_path / "wt"))


def test_runs_in_the_project_subdir_of_the_worktree_and_returns_stdout(tmp_path, task):
    runner = SubprocessRunner(fake_claude(tmp_path, 'echo "$PWD"'), project_subdir="proj", timeout_s=10)

    result = runner.run(task, CONFIG)

    assert Path(result.stdout.strip()).resolve() == (tmp_path / "wt" / "proj").resolve()
    assert result.exit_code == 0


def test_env_overlay_is_merged_over_the_parent_environment(tmp_path, task, monkeypatch):
    monkeypatch.setenv("FACTORY_PARENT_VAR", "kept")
    body = 'echo "$CLAUDE_CODE_DISABLE_CLAUDE_MDS $FACTORY_PARENT_VAR"'
    runner = SubprocessRunner(fake_claude(tmp_path, body), project_subdir="proj", timeout_s=10)

    result = runner.run(task, CONFIG)

    assert result.stdout.strip() == "1 kept"


@pytest.mark.parametrize("name", ["VIRTUAL_ENV", "GIT_DIR", "GIT_WORK_TREE", "ANTHROPIC_API_KEY"])
def test_leaky_variables_are_stripped_from_the_child(tmp_path, task, monkeypatch, name):
    monkeypatch.setenv(name, "leaked")
    runner = SubprocessRunner(fake_claude(tmp_path, f'echo "[${name}]"'), project_subdir="proj", timeout_s=10)

    result = runner.run(task, CONFIG)

    assert result.stdout.strip() == "[]"


def test_non_zero_exit_code_and_stdout_are_preserved(tmp_path, task):
    runner = SubprocessRunner(fake_claude(tmp_path, 'echo partial; exit 3'), project_subdir="proj", timeout_s=10)

    result = runner.run(task, CONFIG)

    assert result.exit_code == 3
    assert result.stdout.strip() == "partial"


def test_timeout_returns_promptly_with_timed_out_even_if_a_grandchild_holds_stdout(tmp_path, task):
    body = "sleep 5 &\necho started\nsleep 5"
    runner = SubprocessRunner(fake_claude(tmp_path, body), project_subdir="proj", timeout_s=0.5)

    started = time.monotonic()
    result = runner.run(task, CONFIG)

    assert time.monotonic() - started < 3
    assert result.timed_out is True
    assert result.stdout.strip() == "started"


def pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_timeout_also_kills_descendants_that_left_the_process_group(tmp_path, task):
    pid_file = tmp_path / "grandchild.pid"
    body = (
        "python3 -c 'import subprocess, sys; "
        'p = subprocess.Popen(["sleep", "30"], start_new_session=True); '
        'open(sys.argv[1], "w").write(str(p.pid)); p.wait()\' ' + str(pid_file)
    )
    runner = SubprocessRunner(fake_claude(tmp_path, body), project_subdir="proj", timeout_s=1.5)

    runner.run(task, CONFIG)

    grandchild = int(pid_file.read_text())
    try:
        deadline = time.monotonic() + 3
        while pid_is_alive(grandchild) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not pid_is_alive(grandchild)
    finally:
        if pid_is_alive(grandchild):
            os.kill(grandchild, signal.SIGKILL)
