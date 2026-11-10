import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path


@contextmanager
def transaction(db_path: Path):
    """One short-lived WAL connection; commits on success, rolls back on error."""
    with closing(sqlite3.connect(db_path, timeout=5)) as conn, conn:
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
