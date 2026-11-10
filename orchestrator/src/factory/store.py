import json
import os
import tempfile
from dataclasses import asdict, dataclass
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from factory.ids import InvalidTaskId, require_safe_id
from factory.task import Task, TaskState


class TaskNotFound(Exception):
    pass


class TaskExists(Exception):
    pass


@dataclass(frozen=True)
class Listing:
    tasks: list[Task]
    skipped: list[str]


def task_to_dict(task: Task) -> dict:
    data = asdict(task)
    data["state"] = task.state.value
    return data


def task_from_dict(data: dict) -> Task:
    data = dict(data)
    data["state"] = TaskState(data["state"])
    data["denials"] = tuple(data["denials"])
    for field in ("audit_findings", "plan_risks", "review_findings", "waived_findings", "prior_audit_findings"):
        data[field] = tuple(data.get(field, ()))
    return Task(**data)


def new_task_id(now: datetime, suffix: str = "") -> str:
    return f"{now:%Y%m%d-%H%M%S}" + (f"-{suffix}" if suffix else "")


def create_with_fresh_id(store: "TaskStore", clock: Callable[[], datetime], build: Callable[[str], Task]) -> Task:
    """Create the task `build(id)` under a timestamp id, adding a numeric suffix if the id is taken."""
    suffix = 1
    while True:
        task = build(new_task_id(clock(), "" if suffix == 1 else str(suffix)))
        try:
            store.create(task)
            return task
        except TaskExists:
            suffix += 1


class TaskStore:
    def __init__(self, directory: Path):
        self._directory = directory

    def save(self, task: Task) -> None:
        self._write_atomically(self._path(task.id), json.dumps(task_to_dict(task)))

    def create(self, task: Task) -> None:
        """Save a new task, never overwriting an existing one."""
        data = task_to_dict(task)
        path = self._path(task.id)
        fd, temp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as temp_file:
                temp_file.write(json.dumps(data))
            try:
                os.link(temp_name, path)
            except FileExistsError:
                raise TaskExists(task.id) from None
        finally:
            Path(temp_name).unlink(missing_ok=True)

    def list(self) -> Listing:
        tasks, skipped = [], []
        for path in sorted(self._directory.glob("*.json")):
            try:
                tasks.append(self.load(path.stem))
            except (InvalidTaskId, ValueError, KeyError, TypeError):
                skipped.append(path.name)
        return Listing(sorted(tasks, key=lambda t: t.id), skipped)

    def load(self, task_id: str) -> Task:
        try:
            data = json.loads(self._path(task_id).read_text())
        except FileNotFoundError:
            raise TaskNotFound(task_id) from None
        return task_from_dict(data)

    def _path(self, task_id: str) -> Path:
        require_safe_id(task_id)
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
