import json
import os
import re
import tempfile
from dataclasses import asdict
from pathlib import Path

from factory.task import Task, TaskState


class TaskNotFound(Exception):
    pass


class InvalidTaskId(Exception):
    pass


_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")


class TaskStore:
    def __init__(self, directory: Path):
        self._directory = directory

    def save(self, task: Task) -> None:
        data = asdict(task)
        data["state"] = task.state.value
        self._write_atomically(self._path(task.id), json.dumps(data))

    def load(self, task_id: str) -> Task:
        try:
            data = json.loads(self._path(task_id).read_text())
        except FileNotFoundError:
            raise TaskNotFound(task_id) from None
        data["state"] = TaskState(data["state"])
        return Task(**data)

    def _path(self, task_id: str) -> Path:
        if not _SAFE_ID.fullmatch(task_id):
            raise InvalidTaskId(task_id)
        return self._directory / f"{task_id}.json"

    @staticmethod
    def _write_atomically(path: Path, text: str) -> None:
        fd, temp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as temp_file:
                temp_file.write(text)
            os.replace(temp_name, path)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
