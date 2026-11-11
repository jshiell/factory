from pathlib import Path

from factory.process_registry import NOT_A_LAUNCHED_GROUP, InvalidRecord, Record, names_a_launched_group
from factory.sqlite_db import transaction


class SqliteProcessRecords:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        with transaction(db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS processes (pid INTEGER PRIMARY KEY, pgid INTEGER NOT NULL, "
                "task_id TEXT NOT NULL, stage TEXT NOT NULL, launched_after REAL NOT NULL, launched_before REAL NOT NULL)"
            )

    def write(self, record: Record) -> None:
        with transaction(self._db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO processes VALUES (?, ?, ?, ?, ?, ?)", tuple(record))

    def load(self) -> list[Record | InvalidRecord]:
        with transaction(self._db_path) as conn:
            rows = conn.execute("SELECT * FROM processes ORDER BY pid").fetchall()
        return [self._checked(Record(*row)) for row in rows]

    def remove(self, pid: int) -> None:
        with transaction(self._db_path) as conn:
            conn.execute("DELETE FROM processes WHERE pid = ?", (pid,))

    def discard(self, invalid: InvalidRecord) -> None:
        self.remove(invalid.pid)

    @staticmethod
    def _checked(record: Record) -> Record | InvalidRecord:
        if names_a_launched_group(record):
            return record
        return InvalidRecord(Path(f"pid {record.pid}"), NOT_A_LAUNCHED_GROUP, record.pid)
