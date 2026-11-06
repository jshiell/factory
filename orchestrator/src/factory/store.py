import json
from dataclasses import asdict
from pathlib import Path

from factory.task import Task, TaskState


class TaskStore:
    def __init__(self, directory: Path):
        self._directory = directory

    def save(self, task: Task) -> None:
        data = asdict(task)
        data["state"] = task.state.value
        self._path(task.id).write_text(json.dumps(data))

    def load(self, task_id: str) -> Task:
        data = json.loads(self._path(task_id).read_text())
        data["state"] = TaskState(data["state"])
        return Task(**data)

    def _path(self, task_id: str) -> Path:
        return self._directory / f"{task_id}.json"
