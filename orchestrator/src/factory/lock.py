import fcntl
import os
from contextlib import contextmanager
from pathlib import Path


class AlreadyRunning(Exception):
    pass


@contextmanager
def state_lock(state_dir):
    """Hold an exclusive, non-blocking lock for the whole `with` block (one driver at a time)."""
    directory = Path(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(directory / "lock", os.O_CREAT | os.O_RDWR)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AlreadyRunning(str(directory)) from None
        os.ftruncate(descriptor, 0)
        os.write(descriptor, str(os.getpid()).encode())
        yield
    finally:
        os.close(descriptor)
