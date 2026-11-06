from dataclasses import dataclass, replace
from enum import Enum


class TaskState(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AGENT_DONE = "agent_done"
    VERIFIED = "verified"
    FAILED = "failed"


class Event(Enum):
    START = "start"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    GATE_PASSED = "gate_passed"
    GATE_FAILED = "gate_failed"


class InvalidTransition(Exception):
    pass


@dataclass(frozen=True)
class Task:
    id: str
    prompt: str
    state: TaskState = TaskState.QUEUED
    attempts: int = 0
    failure_reason: str | None = None
    denials: tuple[dict, ...] = ()
    worktree: str | None = None
    base_sha: str | None = None


_TRANSITIONS = {
    (TaskState.QUEUED, Event.START): TaskState.RUNNING,
    (TaskState.RUNNING, Event.SUCCEEDED): TaskState.AGENT_DONE,
    (TaskState.RUNNING, Event.FAILED): TaskState.FAILED,
    (TaskState.AGENT_DONE, Event.GATE_PASSED): TaskState.VERIFIED,
    (TaskState.AGENT_DONE, Event.GATE_FAILED): TaskState.FAILED,
}


def apply(task: Task, event: Event, reason: str | None = None) -> Task:
    try:
        new_state = _TRANSITIONS[(task.state, event)]
    except KeyError:
        raise InvalidTransition(f"{event.value} not allowed from {task.state.value}")
    attempts = task.attempts + 1 if event is Event.START else task.attempts
    return replace(
        task, state=new_state, attempts=attempts, failure_reason=reason
    )
