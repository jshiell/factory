import os
import signal
import subprocess
import sys
import time

import pytest

from factory.process import group_alive, kill_tree, run_to_exit
from factory.process_registry import Tracker, load_records


def python(code):
    return [sys.executable, "-c", code]


def test_the_tail_of_the_combined_output_is_returned_up_to_the_limit(tmp_path):
    code = "import sys; print('x' * 5000, flush=True); print('to stderr', file=sys.stderr, flush=True); print('last line')"

    result = run_to_exit(python(code), tmp_path, None, 10, capture_limit=100)

    assert result.exit_code == 0 and result.timed_out is False
    assert len(result.tail) <= 100
    assert result.tail.endswith("last line\n") and "to stderr" in result.tail


def test_without_a_limit_nothing_is_captured(tmp_path):
    result = run_to_exit(python("print('hi')"), tmp_path, None, 10)

    assert result.tail is None


def test_invalid_utf8_in_the_output_is_replaced_not_raised(tmp_path):
    code = "import sys; sys.stdout.buffer.write(b'ok \\xff\\xfe end')"

    result = run_to_exit(python(code), tmp_path, None, 10, capture_limit=100)

    assert result.tail.startswith("ok ") and result.tail.endswith(" end")


def test_a_timeout_still_kills_the_tree_while_capturing(tmp_path):
    marker = tmp_path / "pid"
    code = f"import os, time; open({str(marker)!r}, 'w').write(str(os.getpid())); print('before', flush=True); time.sleep(30)"

    result = run_to_exit(python(code), tmp_path, None, 1, capture_limit=100)

    assert result.timed_out is True and result.exit_code is None
    assert "before" in result.tail
    pid = int(marker.read_text())
    try:
        os.kill(pid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    assert not alive


def test_a_group_is_alive_until_its_members_are_killed_and_reaped():
    leader = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        assert group_alive(leader.pid) is True
        kill_tree(leader.pid)
        leader.wait()
        assert group_alive(leader.pid) is False
    finally:
        leader.kill()


def test_a_group_stays_alive_while_a_member_lives_after_its_leader_is_gone():
    leader = subprocess.Popen(["sh", "-c", "sleep 30 & exit 0"], start_new_session=True)
    leader.wait()
    try:
        assert group_alive(leader.pid) is True
    finally:
        os.killpg(leader.pid, signal.SIGKILL)


def test_a_normal_exit_still_kills_what_the_command_left_running(tmp_path):
    result = run_to_exit(["sh", "-c", "sleep 30 & echo $!"], tmp_path, None, 10, capture_limit=100)

    lingering = int(result.tail.strip())
    deadline = time.time() + 5
    while time.time() < deadline and _alive(lingering):
        time.sleep(0.05)
    assert result.exit_code == 0 and not _alive(lingering)


def _alive(pid):
    listed = subprocess.run(["pgrep", "-x", "sleep"], capture_output=True, text=True).stdout.split()
    return str(pid) in listed


LIST_RECORDS = "import os, sys; print(os.getpid(), sorted(os.listdir(sys.argv[1])))"


def test_the_command_is_recorded_while_it_runs_and_the_record_goes_after_a_normal_exit(tmp_path):
    result = run_to_exit([sys.executable, "-c", LIST_RECORDS, str(tmp_path / "procs")], tmp_path, None, 10,
                         capture_limit=200, tracker=Tracker(tmp_path, "t1", "gate"))

    pid, listing = result.tail.split(" ", 1)
    assert f"{pid}.json" in listing
    assert load_records(tmp_path) == []


def test_the_record_goes_after_a_timeout(tmp_path):
    result = run_to_exit(["sleep", "30"], tmp_path, None, 0.2, tracker=Tracker(tmp_path, "t1", "gate"))

    assert result.timed_out and load_records(tmp_path) == []


def test_the_record_goes_after_an_interruption(tmp_path):
    def interrupt(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGALRM, interrupt)
    signal.setitimer(signal.ITIMER_REAL, 0.3)
    try:
        with pytest.raises(KeyboardInterrupt):
            run_to_exit(["sleep", "30"], tmp_path, None, 30, tracker=Tracker(tmp_path, "t1", "gate"))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)

    assert load_records(tmp_path) == []


class UnwritableRecords:
    def write(self, record):
        raise OSError("database is locked")

    def remove(self, pid):
        raise AssertionError("nothing was recorded, so nothing is removed")


def _running(marker):
    return subprocess.run(["pgrep", "-f", marker], capture_output=True).returncode == 0


def test_a_record_that_cannot_be_written_kills_the_launched_command_and_raises(tmp_path):
    with pytest.raises(OSError, match="database is locked"):
        run_to_exit(["sleep", "29.4711"], tmp_path, None, 30, tracker=Tracker(UnwritableRecords(), "t1", "gate"))

    assert not _running("sleep 29.4711")


class StuckRecords:
    def write(self, record):
        pass

    def remove(self, pid):
        raise OSError("database is locked")


def test_a_record_that_cannot_be_removed_after_the_command_is_dead_is_logged_and_the_run_still_succeeds(tmp_path, caplog):
    result = run_to_exit(["true"], tmp_path, None, 10, tracker=Tracker(StuckRecords(), "t1", "gate"))

    assert result.exit_code == 0
    assert "could not remove the process record" in caplog.text and "database is locked" in caplog.text
