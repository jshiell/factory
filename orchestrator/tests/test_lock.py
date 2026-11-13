import os
import subprocess
import sys

import pytest

from factory.lock import AlreadyRunning, state_lock

HOLDER = """
import sys
from factory.lock import state_lock
with state_lock(sys.argv[1]):
    print("held", flush=True)
    sys.stdin.readline()
"""


def start_holder(directory):
    process = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(directory)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    assert process.stdout.readline().strip() == "held"
    return process


def release(process):
    process.stdin.write("\n")
    process.stdin.flush()
    process.wait(timeout=10)


def test_the_lock_creates_the_directory_and_records_the_pid(tmp_path):
    directory = tmp_path / "new" / "state"

    with state_lock(directory):
        assert (directory / "lock").read_text() == str(os.getpid())


def test_a_named_lock_uses_its_own_file_and_coexists_with_the_default_lock(tmp_path):
    with state_lock(tmp_path):
        with state_lock(tmp_path, name="watch"):
            assert (tmp_path / "watch").read_text() == str(os.getpid())


def test_a_second_holder_of_the_same_named_lock_is_refused(tmp_path):
    with state_lock(tmp_path, name="watch"):
        with pytest.raises(AlreadyRunning):
            with state_lock(tmp_path, name="watch"):
                pass


def test_a_second_process_cannot_take_the_lock_while_it_is_held(tmp_path):
    holder = start_holder(tmp_path)
    try:
        with pytest.raises(AlreadyRunning):
            with state_lock(tmp_path):
                pass
    finally:
        release(holder)


def test_the_lock_is_released_on_exit_and_on_exception(tmp_path):
    with state_lock(tmp_path):
        pass
    with pytest.raises(ValueError):
        with state_lock(tmp_path):
            raise ValueError
    with state_lock(tmp_path):
        pass


def test_the_lock_is_free_again_after_the_holder_process_ends(tmp_path):
    release(start_holder(tmp_path))

    with state_lock(tmp_path):
        pass


def test_acquiring_never_truncates_the_lock_file(tmp_path):
    (tmp_path / "lock").write_text("keep")
    holder = start_holder(tmp_path)
    try:
        with pytest.raises(AlreadyRunning):
            with state_lock(tmp_path):
                pass
        assert (tmp_path / "lock").read_text() == str(holder.pid)
    finally:
        release(holder)
