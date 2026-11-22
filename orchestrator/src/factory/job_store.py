import json
import logging
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from factory.sqlite_db import transaction


class JobNotFound(Exception):
    pass


@dataclass(frozen=True)
class Job:
    id: str
    kind: str
    task_id: str | None
    state: str
    result: dict | None
    error: str | None


class JobStore:
    def __init__(self, db_path: Path, on_change: Callable[[], None] | None = None):
        """`on_change` is called after every committed state write, on the writer's thread."""
        self._db_path = db_path
        self._on_change = on_change
        with transaction(db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS jobs (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, "
                "kind TEXT NOT NULL, task_id TEXT, state TEXT NOT NULL, result TEXT, error TEXT)"
            )

    def create(self, kind: str, task_id: str | None) -> Job:
        job_id = secrets.token_hex(8)
        with transaction(self._db_path) as conn:
            conn.execute("INSERT INTO jobs (id, kind, task_id, state) VALUES (?, ?, ?, 'queued')", (job_id, kind, task_id))
        self._changed()
        return self.get(job_id)

    def get(self, job_id: str) -> Job:
        with transaction(self._db_path) as conn:
            row = conn.execute("SELECT id, kind, task_id, state, result, error FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        return self._job(*row)

    def list(self) -> list[Job]:
        with transaction(self._db_path) as conn:
            rows = conn.execute("SELECT id, kind, task_id, state, result, error FROM jobs ORDER BY seq").fetchall()
        return [self._job(*row) for row in rows]

    def latest_for_task(self, task_id: str) -> Job | None:
        with transaction(self._db_path) as conn:
            row = conn.execute(
                "SELECT id, kind, task_id, state, result, error FROM jobs WHERE task_id = ? ORDER BY seq DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        return self._job(*row) if row else None

    def unfinished_for_task(self, task_id: str) -> Job | None:
        with transaction(self._db_path) as conn:
            row = conn.execute(
                "SELECT id, kind, task_id, state, result, error FROM jobs "
                "WHERE task_id = ? AND state IN ('queued', 'running') ORDER BY seq DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        return self._job(*row) if row else None

    def mark_running(self, job_id: str) -> None:
        self._update(job_id, "running")

    def mark_succeeded(self, job_id: str, result: dict) -> None:
        self._update(job_id, "succeeded", result=json.dumps(result))

    def mark_failed(self, job_id: str, error: str) -> None:
        self._update(job_id, "failed", error=error)

    def interrupt_unfinished(self) -> None:
        with transaction(self._db_path) as conn:
            conn.execute("UPDATE jobs SET state = 'interrupted' WHERE state IN ('queued', 'running')")
        self._changed()

    def _update(self, job_id: str, state: str, result: str | None = None, error: str | None = None) -> None:
        with transaction(self._db_path) as conn:
            conn.execute("UPDATE jobs SET state = ?, result = ?, error = ? WHERE id = ?", (state, result, error, job_id))
        self._changed()

    def _changed(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change()
        except Exception:  # a committed write, and the job worker making it, must not fail because a listener did
            logging.getLogger(__name__).warning("a job store listener failed", exc_info=True)

    @staticmethod
    def _job(job_id, kind, task_id, state, result, error) -> Job:
        return Job(job_id, kind, task_id, state, json.loads(result) if result else None, error)
