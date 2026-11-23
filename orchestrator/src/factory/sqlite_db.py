import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path

_BUSY_TIMEOUT_S = 5
_WAL_RETRY_INTERVAL_S = 0.05


@contextmanager
def transaction(db_path: Path):
    """One short-lived WAL connection; commits on success, rolls back on error."""
    with closing(sqlite3.connect(db_path, timeout=_BUSY_TIMEOUT_S)) as conn, conn:
        _ensure_wal(conn)
        yield conn


def _ensure_wal(conn: sqlite3.Connection) -> None:
    # Switching a rollback-mode file to WAL returns SQLITE_BUSY at once, ignoring
    # the busy timeout, while another connection holds RESERVED (deadlock
    # avoidance). So retry until the busy timeout's deadline. Once the file is WAL
    # the setting persists, so the switch is skipped and cannot fail.
    if _journal_mode(conn) == "wal":
        return
    deadline = time.monotonic() + _BUSY_TIMEOUT_S
    while True:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as error:
            if not _is_locked(error) or time.monotonic() >= deadline:
                raise
            time.sleep(_WAL_RETRY_INTERVAL_S)


def _journal_mode(conn: sqlite3.Connection) -> str:
    return conn.execute("PRAGMA journal_mode").fetchone()[0].lower()


def _is_locked(error: sqlite3.OperationalError) -> bool:
    return "database is locked" in str(error)
