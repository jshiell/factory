from dataclasses import dataclass, replace
from enum import Enum


class TaskState(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AGENT_DONE = "agent_done"
    FAILED = "failed"


class Event(Enum):
    START = "start"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class Task:
    id: str
    prompt: str
    state: TaskState = TaskState.QUEUED
    attempts: int = 0
    failure_reason: str | None = None


def apply(task: Task, event: Event) -> Task:
    return replace(task, state=TaskState.RUNNING, attempts=task.attempts + 1)
