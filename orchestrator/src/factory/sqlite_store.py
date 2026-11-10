import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from factory.ids import require_safe_id
from factory.store import Listing, TaskExists, TaskNotFound, task_from_dict, task_to_dict
from factory.task import Task


class SqliteTaskStore:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        with self._transaction() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, state TEXT NOT NULL, data TEXT NOT NULL)")

    def save(self, task: Task) -> None:
        require_safe_id(task.id)
        with self._transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO tasks (id, state, data) VALUES (?, ?, ?)",
                (task.id, task.state.value, json.dumps(task_to_dict(task))),
            )

    def create(self, task: Task) -> None:
        """Save a new task, never overwriting an existing one."""
        require_safe_id(task.id)
        try:
            with self._transaction() as conn:
                conn.execute(
                    "INSERT INTO tasks (id, state, data) VALUES (?, ?, ?)",
                    (task.id, task.state.value, json.dumps(task_to_dict(task))),
                )
        except sqlite3.IntegrityError:
            raise TaskExists(task.id) from None

    def list(self) -> Listing:
        with self._transaction() as conn:
            rows = conn.execute("SELECT id, data FROM tasks ORDER BY id").fetchall()
        tasks, skipped = [], []
        for task_id, data in rows:
            try:
                tasks.append(task_from_dict(json.loads(data)))
            except (ValueError, KeyError, TypeError):
                skipped.append(task_id)
        return Listing(tasks, skipped)

    def load(self, task_id: str) -> Task:
        require_safe_id(task_id)
        with self._transaction() as conn:
            row = conn.execute("SELECT data FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise TaskNotFound(task_id)
        return task_from_dict(json.loads(row[0]))

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @contextmanager
    def _transaction(self):
        with closing(self._connect()) as conn, conn:
            yield conn
