import os
import signal
import stat
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest

from factory.command import RunnerConfig
from factory.harness import Capability, Invocation
from factory.process_registry import load_records
from factory.runner import SubprocessRunner
from factory.sandbox import SANDBOXES, NonoSandbox
from factory.step import TAIL_CHARS
from factory.task import Task, TaskState
from slowrecords import WAIT_FOR_RECORD, RecordsWrittenLate

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


def test_a_default_runner_asks_the_default_sandbox_for_an_unset_executable(tmp_path, monkeypatch):
    """Call-shape test: pins that no literal executable name lives outside the sandbox itself."""
    asked = []
    monkeypatch.setitem(SANDBOXES, "nono", lambda executable: asked.append(executable) or NonoSandbox(executable))

    SubprocessRunner(fake_claude(tmp_path, "echo ok"), project_subdir="proj", timeout_s=10)

    assert asked == [None]


def test_env_overlay_is_merged_over_the_parent_environment(tmp_path, task, monkeypatch):
    monkeypatch.setenv("FACTORY_PARENT_VAR", "kept")
    body = 'echo "$CLAUDE_CODE_DISABLE_CLAUDE_MDS $FACTORY_PARENT_VAR"'
    runner = SubprocessRunner(fake_claude(tmp_path, body), project_subdir="proj", timeout_s=10)

    result = runner.run(task, CONFIG)

    assert result.stdout.strip() == "1 kept"


@pytest.mark.parametrize("name", ["VIRTUAL_ENV", "GIT_DIR", "GIT_WORK_TREE", "ANTHROPIC_API_KEY",
                                  "GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"])
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


def test_stderr_is_returned(tmp_path, task):
    runner = SubprocessRunner(fake_claude(tmp_path, "echo boom >&2; exit 1"), project_subdir="proj", timeout_s=10)

    result = runner.run(task, CONFIG)

    assert "boom" in result.stderr


def test_a_long_stderr_is_cut_to_its_tail(tmp_path, task):
    body = f"i=0; while [ $i -lt {TAIL_CHARS * 2} ]; do printf x >&2; i=$((i+1)); done; echo END >&2"
    runner = SubprocessRunner(fake_claude(tmp_path, body), project_subdir="proj", timeout_s=30)

    result = runner.run(task, CONFIG)

    assert len(result.stderr) == TAIL_CHARS
    assert result.stderr.endswith("xEND\n")


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


def test_an_interruption_while_waiting_kills_the_whole_tree_and_re_raises(tmp_path, task, monkeypatch):
    pid_file = tmp_path / "grandchild.pid"
    body = (
        "python3 -c 'import subprocess, sys; "
        'p = subprocess.Popen(["sleep", "30"], start_new_session=True); '
        'open(sys.argv[1], "w").write(str(p.pid)); p.wait()\' ' + str(pid_file)
    )
    started = []

    class InterruptingPopen(subprocess.Popen):
        interrupted = False

        def wait(self, timeout=None):
            if not InterruptingPopen.interrupted:
                InterruptingPopen.interrupted = True
                started.append(self.pid)
                deadline = time.monotonic() + 5
                while not (pid_file.exists() and pid_file.read_text()) and time.monotonic() < deadline:
                    time.sleep(0.05)
                raise KeyboardInterrupt
            return super().wait(timeout)

    monkeypatch.setattr("factory.runner.subprocess.Popen", InterruptingPopen)
    runner = SubprocessRunner(fake_claude(tmp_path, body), project_subdir="proj", timeout_s=30)

    with pytest.raises(KeyboardInterrupt):
        runner.run(task, CONFIG)

    grandchild = int(pid_file.read_text())
    try:
        deadline = time.monotonic() + 3
        while (pid_is_alive(started[0]) or pid_is_alive(grandchild)) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not pid_is_alive(started[0]) and not pid_is_alive(grandchild)
    finally:
        for pid in (started[0], grandchild):
            if pid_is_alive(pid):
                os.kill(pid, signal.SIGKILL)


def fake_wrapper(tmp_path: Path, log: Path) -> str:
    script = tmp_path / "fake-nono"
    script.write_text(
        "#!/bin/sh\n"
        f'for a in "$@"; do printf \'%s\\n\' "$a" >> {log}; done\n'
        'while [ "$1" != "--" ]; do shift; done\nshift\nexec "$@"\n'
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_a_confined_config_runs_claude_through_the_sandbox_wrapper_with_the_worktree_granted(tmp_path, task):
    log = tmp_path / "wrapper.log"
    claude = fake_claude(tmp_path, 'echo "ran $1"')
    runner = SubprocessRunner(
        claude, project_subdir="proj", timeout_s=10, sandbox=NonoSandbox(fake_wrapper(tmp_path, log))
    )
    worktree = str(Path(task.worktree).resolve())

    result = runner.run(task, replace(CONFIG, confine=True))

    lines = log.read_text().splitlines()
    assert result.stdout.strip() == "ran -p"
    assert lines[lines.index("--read") + 1] == worktree
    assert lines[lines.index("--") + 1] == claude
    assert lines[lines.index("--add-dir") + 1] == worktree
    assert lines.index("--") < lines.index("--add-dir")


def sandbox_reads(log: Path) -> list[str]:
    """The paths the wrapper was asked to grant read access to, in order (before the `--`)."""
    lines = log.read_text().splitlines()
    before_command = lines[: lines.index("--")]
    return [before_command[i + 1] for i, arg in enumerate(before_command) if arg == "--read"]


def test_a_confined_run_grants_the_directory_the_claude_executable_is_found_in(tmp_path, task):
    versioned = tmp_path / "install" / "2.1.325"
    versioned.mkdir(parents=True)
    fake_claude(versioned, "echo ok")
    (tmp_path / "install" / "latest").symlink_to(versioned)
    via_link = tmp_path / "install" / "latest" / "fake-claude"
    log = tmp_path / "wrapper.log"
    runner = SubprocessRunner(
        str(via_link), project_subdir="proj", timeout_s=10, sandbox=NonoSandbox(fake_wrapper(tmp_path, log))
    )

    runner.run(task, replace(CONFIG, confine=True))

    assert sandbox_reads(log) == [str(Path(task.worktree).resolve()), str(tmp_path / "install" / "latest")]


def symlinked_claude(tmp_path):
    """`bin/fake-claude` is a symlink to `release/fake-claude`; returns (link, release directory)."""
    release = tmp_path / "release"
    release.mkdir()
    fake_claude(release, "echo ok")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "fake-claude").symlink_to(release / "fake-claude")
    return tmp_path / "bin" / "fake-claude", release


def test_a_symlinked_claude_file_is_granted_both_its_own_and_its_resolved_directory(tmp_path, task):
    tmp_path = tmp_path.resolve()
    link, release = symlinked_claude(tmp_path)
    log = tmp_path / "wrapper.log"
    runner = SubprocessRunner(
        str(link), project_subdir="proj", timeout_s=10, sandbox=NonoSandbox(fake_wrapper(tmp_path, log)),
    )

    result = runner.run(task, replace(CONFIG, confine=True))

    assert sandbox_reads(log) == [str(Path(task.worktree).resolve()), str(tmp_path / "bin"), str(release)]
    assert result.stdout.strip() == "ok"


def test_the_resolved_directory_is_refused_when_it_contains_a_private_directory(tmp_path, task):
    tmp_path = tmp_path.resolve()
    link, release = symlinked_claude(tmp_path)
    log = tmp_path / "wrapper.log"
    runner = SubprocessRunner(
        str(link), project_subdir="proj", timeout_s=10, sandbox=NonoSandbox(fake_wrapper(tmp_path, log)),
        private_dirs=(str(release / "state"),),
    )

    runner.run(task, replace(CONFIG, confine=True))

    assert sandbox_reads(log) == [str(Path(task.worktree).resolve()), str(tmp_path / "bin")]


def test_a_plain_claude_file_is_granted_exactly_one_directory(tmp_path, task):
    tmp_path = tmp_path.resolve()
    fake_claude(tmp_path, "echo ok")
    log = tmp_path / "wrapper.log"
    runner = SubprocessRunner(
        str(tmp_path / "fake-claude"), project_subdir="proj", timeout_s=10, sandbox=NonoSandbox(fake_wrapper(tmp_path, log)),
    )

    runner.run(task, replace(CONFIG, confine=True))

    assert sandbox_reads(log) == [str(Path(task.worktree).resolve()), str(tmp_path)]


def test_a_symlink_within_the_same_directory_is_granted_one_directory(tmp_path, task):
    tmp_path = tmp_path.resolve()
    fake_claude(tmp_path, "echo ok")
    (tmp_path / "claude").symlink_to(tmp_path / "fake-claude")
    log = tmp_path / "wrapper.log"
    runner = SubprocessRunner(
        str(tmp_path / "claude"), project_subdir="proj", timeout_s=10, sandbox=NonoSandbox(fake_wrapper(tmp_path, log)),
    )

    runner.run(task, replace(CONFIG, confine=True))

    assert sandbox_reads(log) == [str(Path(task.worktree).resolve()), str(tmp_path)]


def test_a_resolved_directory_that_is_home_is_not_granted(tmp_path, task, monkeypatch):
    tmp_path = tmp_path.resolve()
    link, release = symlinked_claude(tmp_path)
    monkeypatch.setenv("HOME", str(release))
    log = tmp_path / "wrapper.log"
    runner = SubprocessRunner(
        str(link), project_subdir="proj", timeout_s=10, sandbox=NonoSandbox(fake_wrapper(tmp_path, log)),
    )

    runner.run(task, replace(CONFIG, confine=True))

    assert sandbox_reads(log) == [str(Path(task.worktree).resolve()), str(tmp_path / "bin")]


def test_a_refused_unresolved_directory_still_leaves_the_resolved_one_granted(tmp_path, task):
    tmp_path = tmp_path.resolve()
    link, release = symlinked_claude(tmp_path)
    log = tmp_path / "wrapper.log"
    runner = SubprocessRunner(
        str(link), project_subdir="proj", timeout_s=10, sandbox=NonoSandbox(fake_wrapper(tmp_path, log)),
        private_dirs=(str(tmp_path / "bin" / "state"),),
    )

    runner.run(task, replace(CONFIG, confine=True))

    assert sandbox_reads(log) == [str(Path(task.worktree).resolve()), str(release)]


def test_a_claude_executable_that_cannot_be_found_adds_no_grant_and_the_run_goes_ahead(tmp_path, task):
    log = tmp_path / "wrapper.log"
    runner = SubprocessRunner(
        "no-such-claude-anywhere", project_subdir="proj", timeout_s=10, sandbox=NonoSandbox(fake_wrapper(tmp_path, log))
    )

    result = runner.run(task, replace(CONFIG, confine=True))

    assert sandbox_reads(log) == [str(Path(task.worktree).resolve())]
    assert result.exit_code == 127


def test_no_grant_is_made_for_a_directory_that_contains_a_private_directory(tmp_path, task):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_claude(bin_dir, "echo ok")
    log = tmp_path / "wrapper.log"
    runner = SubprocessRunner(
        str(bin_dir / "fake-claude"), project_subdir="proj", timeout_s=10,
        sandbox=NonoSandbox(fake_wrapper(tmp_path, log)), private_dirs=(str(bin_dir / "state"),),
    )

    result = runner.run(task, replace(CONFIG, confine=True))

    assert sandbox_reads(log) == [str(Path(task.worktree).resolve())]
    assert result.stdout.strip() == "ok"


def test_no_grant_is_made_for_the_home_directory(tmp_path, task, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    fake_claude(home, "echo ok")
    monkeypatch.setenv("HOME", str(home))
    log = tmp_path / "wrapper.log"
    runner = SubprocessRunner(
        str(home / "fake-claude"), project_subdir="proj", timeout_s=10,
        sandbox=NonoSandbox(fake_wrapper(tmp_path, log)),
    )

    result = runner.run(task, replace(CONFIG, confine=True))

    assert sandbox_reads(log) == [str(Path(task.worktree).resolve())]
    assert result.stdout.strip() == "ok"


def test_an_unconfined_config_does_not_touch_the_wrapper(tmp_path, task):
    log = tmp_path / "wrapper.log"
    runner = SubprocessRunner(
        fake_claude(tmp_path, "echo direct"), project_subdir="proj", timeout_s=10,
        sandbox=NonoSandbox(fake_wrapper(tmp_path, log)),
    )

    result = runner.run(task, CONFIG)

    assert result.stdout.strip() == "direct"
    assert not log.exists()


def test_a_missing_sandbox_wrapper_raises_and_claude_never_runs_unconfined(tmp_path, task):
    marker = tmp_path / "claude-ran"
    runner = SubprocessRunner(
        fake_claude(tmp_path, f"touch {marker}"), project_subdir="proj", timeout_s=10,
        sandbox=NonoSandbox(str(tmp_path / "no-such-nono")),
    )

    with pytest.raises(FileNotFoundError):
        runner.run(task, replace(CONFIG, confine=True))

    assert not marker.exists()


def test_a_wrapper_that_fails_before_launching_claude_surfaces_its_exit_code_with_empty_stdout(tmp_path, task):
    marker = tmp_path / "claude-ran"
    failing = tmp_path / "failing-nono"
    failing.write_text("#!/bin/sh\necho 'profile not found' >&2\nexit 1\n")
    failing.chmod(failing.stat().st_mode | stat.S_IEXEC)
    runner = SubprocessRunner(
        fake_claude(tmp_path, f"touch {marker}"), project_subdir="proj", timeout_s=10,
        sandbox=NonoSandbox(str(failing)),
    )

    result = runner.run(task, replace(CONFIG, confine=True))

    assert (result.exit_code, result.stdout) == (1, "")
    assert "profile not found" in result.stderr
    assert not marker.exists()


def test_timeout_kills_the_whole_tree_through_a_wrapper_that_stays_as_a_parent(tmp_path, task):
    pid_file = tmp_path / "grandchild.pid"
    body = (
        "python3 -c 'import subprocess, sys; "
        'p = subprocess.Popen(["sleep", "30"], start_new_session=True); '
        'open(sys.argv[1], "w").write(str(p.pid)); p.wait()\' ' + str(pid_file)
    )
    forking = tmp_path / "forking-nono"
    forking.write_text('#!/bin/sh\nwhile [ "$1" != "--" ]; do shift; done\nshift\n"$@"\n')
    forking.chmod(forking.stat().st_mode | stat.S_IEXEC)
    runner = SubprocessRunner(
        fake_claude(tmp_path, body), project_subdir="proj", timeout_s=1.5, sandbox=NonoSandbox(str(forking))
    )

    result = runner.run(task, replace(CONFIG, confine=True))

    grandchild = int(pid_file.read_text())
    try:
        deadline = time.monotonic() + 3
        while pid_is_alive(grandchild) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert result.timed_out and not pid_is_alive(grandchild)
    finally:
        if pid_is_alive(grandchild):
            os.kill(grandchild, signal.SIGKILL)


def test_a_wrapper_banner_on_stderr_does_not_reach_the_captured_stdout(tmp_path, task):
    noisy = tmp_path / "noisy-nono"
    noisy.write_text(
        '#!/bin/sh\necho "nono banner" >&2\nwhile [ "$1" != "--" ]; do shift; done\nshift\nexec "$@"\n'
    )
    noisy.chmod(noisy.stat().st_mode | stat.S_IEXEC)
    runner = SubprocessRunner(
        fake_claude(tmp_path, "echo '{\"ok\": true}'"), project_subdir="proj", timeout_s=10,
        sandbox=NonoSandbox(str(noisy)),
    )

    result = runner.run(task, replace(CONFIG, confine=True))

    assert result.stdout == '{"ok": true}\n'


def test_a_normal_exit_still_kills_what_the_agent_left_running(tmp_path, task):
    runner = SubprocessRunner(fake_claude(tmp_path, "sleep 30 &\necho $!"), project_subdir="proj", timeout_s=10)

    result = runner.run(task, CONFIG)

    lingering = result.stdout.strip()
    deadline = time.time() + 5
    while time.time() < deadline and lingering in _sleepers():
        time.sleep(0.05)
    assert lingering not in _sleepers()


def _sleepers():
    return subprocess.run(["pgrep", "-x", "sleep"], capture_output=True, text=True).stdout.split()


def test_the_agent_is_recorded_under_the_task_state_while_it_runs_and_the_record_goes_after(tmp_path, task, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("STATE", str(state))
    runner = SubprocessRunner(fake_claude(tmp_path, WAIT_FOR_RECORD + 'ls "$STATE/procs"'), project_subdir="proj", timeout_s=10, records=RecordsWrittenLate(state))

    result = runner.run(replace(task, state=TaskState.QUEUED), CONFIG)

    (name,) = result.stdout.split()
    assert name.endswith(".json")
    assert load_records(state) == []


def test_the_agent_record_names_the_task_and_its_state(tmp_path, task, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv("STATE", str(state))
    runner = SubprocessRunner(fake_claude(tmp_path, WAIT_FOR_RECORD + 'cat "$STATE/procs/"*.json'), project_subdir="proj", timeout_s=10, records=RecordsWrittenLate(state))

    result = runner.run(replace(task, state=TaskState.QUEUED), CONFIG)

    assert '"task_id": "t1"' in result.stdout and '"stage": "queued"' in result.stdout


def test_guard_an_agent_whose_record_cannot_be_written_is_killed_and_the_run_raises(tmp_path, task):
    class UnwritableRecords:
        def write(self, record):
            raise OSError("database is locked")

    runner = SubprocessRunner(fake_claude(tmp_path, "exec sleep 29.4712"), project_subdir="proj", timeout_s=10, records=UnwritableRecords())

    with pytest.raises(OSError, match="database is locked"):
        runner.run(replace(task, state=TaskState.QUEUED), CONFIG)

    assert subprocess.run(["pgrep", "-f", "sleep 29.4712"], capture_output=True).returncode != 0


def test_each_plugin_dir_is_granted_read_only_to_the_sandbox(tmp_path, task):
    log = tmp_path / "wrapper.log"
    runner = SubprocessRunner(
        fake_claude(tmp_path, "echo ok"), project_subdir="proj", timeout_s=10,
        sandbox=NonoSandbox(fake_wrapper(tmp_path, log)),
    )

    runner.run(task, replace(CONFIG, confine=True, plugin_dirs=("/h/p a", "/h/p b")))

    lines = log.read_text().splitlines()
    assert sandbox_reads(log)[1:3] == ["/h/p a", "/h/p b"]
    assert [lines[i + 1] for i, arg in enumerate(lines) if arg == "--plugin-dir"] == ["/h/p a", "/h/p b"]


class EchoHarness:
    """Starts `<executable> <prompt>` with its own env; never parsed."""

    capabilities = frozenset(Capability)

    def build(self, prompt, config, executable, add_dirs=()):
        return Invocation([executable, prompt, *add_dirs], {"HARNESS_MARK": "from-fake"})

    def parse(self, stdout, exit_code):
        raise AssertionError("not parsed in these tests")


def test_a_run_launches_the_argv_and_env_the_harness_builds(tmp_path, task):
    script = fake_claude(tmp_path, 'echo "$1 $HARNESS_MARK"')
    runner = SubprocessRunner(script, project_subdir="proj", timeout_s=10, harness=EchoHarness())

    result = runner.run(task, CONFIG)

    assert result.stdout.strip() == "do it from-fake"


def test_a_result_carries_the_parser_of_the_harness_that_started_the_run(tmp_path, task):
    harness = EchoHarness()
    harness.parse = lambda stdout, exit_code: ("parsed by the fake", stdout.strip(), exit_code)
    runner = SubprocessRunner(fake_claude(tmp_path, "echo hi"), project_subdir="proj", timeout_s=10, harness=harness)

    result = runner.run(task, CONFIG)

    assert result.parse(result.stdout, result.exit_code) == ("parsed by the fake", "hi", 0)


def test_a_confined_run_asks_the_sandbox_for_the_profile_the_harness_names(tmp_path, task):
    log = tmp_path / "wrapper.log"
    harness = EchoHarness()
    harness.sandbox_profile = "acme/fake"
    runner = SubprocessRunner(
        fake_claude(tmp_path, "echo ok"), project_subdir="proj", timeout_s=10,
        sandbox=NonoSandbox(fake_wrapper(tmp_path, log)), harness=harness,
    )

    runner.run(task, replace(CONFIG, confine=True))

    lines = log.read_text().splitlines()
    assert lines[lines.index("--profile") + 1] == "acme/fake"


def test_a_confined_run_passes_no_profile_when_the_harness_has_none(tmp_path, task):
    log = tmp_path / "wrapper.log"
    harness = EchoHarness()
    harness.sandbox_profile = None
    runner = SubprocessRunner(
        fake_claude(tmp_path, "echo ok"), project_subdir="proj", timeout_s=10,
        sandbox=NonoSandbox(fake_wrapper(tmp_path, log)), harness=harness,
    )

    runner.run(task, replace(CONFIG, confine=True))

    assert "--profile" not in log.read_text().splitlines()


class RecordingSandbox:
    def __init__(self, prefix: list[str]):
        self.prefix = prefix
        self.policies = []

    def wrap(self, policy):
        self.policies.append(policy)
        return self.prefix


def test_a_confined_run_is_wrapped_by_the_given_sandbox_with_the_policy_the_runner_derives(tmp_path, task):
    log = tmp_path / "wrapper.log"
    claude = fake_claude(tmp_path, "echo ok")
    harness = EchoHarness()
    harness.sandbox_profile = "acme/fake"
    sandbox = RecordingSandbox([fake_wrapper(tmp_path, log), "--"])
    runner = SubprocessRunner(
        claude, project_subdir="proj", timeout_s=10, sandbox=sandbox, harness=harness,
    )

    result = runner.run(task, replace(CONFIG, confine=True, plugin_dirs=("/plug",)))

    (policy,) = sandbox.policies
    assert (policy.worktree, policy.profile, policy.write) == (
        str(Path(task.worktree).resolve()), "acme/fake", (str(Path(task.worktree).resolve()),)
    )
    assert "/plug" in policy.read and str(Path(claude).parent) in policy.read
    assert result.exit_code == 0 and log.exists()


def test_a_confined_run_names_its_launch_directory_in_the_policy(tmp_path, task):
    harness = EchoHarness()
    harness.sandbox_profile = "acme/fake"
    sandbox = RecordingSandbox([fake_wrapper(tmp_path, tmp_path / "wrapper.log"), "--"])
    runner = SubprocessRunner(
        fake_claude(tmp_path, "echo ok"), project_subdir="proj", timeout_s=10, sandbox=sandbox, harness=harness,
    )

    runner.run(task, replace(CONFIG, confine=True))

    (policy,) = sandbox.policies
    assert policy.cwd == Path(task.worktree) / "proj"
