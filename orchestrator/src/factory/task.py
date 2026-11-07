from dataclasses import dataclass, replace
from enum import Enum


class TaskState(Enum):
    PROPOSED = "proposed"
    PLANNED = "planned"
    REVIEWED = "reviewed"
    QUEUED = "queued"
    RUNNING = "running"
    AGENT_DONE = "agent_done"
    VERIFIED = "verified"
    AUDITED = "audited"
    MERGED = "merged"
    FAILED = "failed"


class Event(Enum):
    START = "start"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    GATE_PASSED = "gate_passed"
    GATE_FAILED = "gate_failed"
    AUDIT_PASSED = "audit_passed"
    AUDIT_FAILED = "audit_failed"
    PLAN_DONE = "plan_done"
    PLAN_FAILED = "plan_failed"
    REVIEW_PASSED = "review_passed"
    REVIEW_FAILED = "review_failed"
    APPROVED = "approved"
    HUMAN_REJECTED = "human_rejected"
    MERGED = "merged"
    ABANDONED = "abandoned"


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
    audit_findings: tuple[str, ...] = ()
    audited_sha: str | None = None
    audit_error: str | None = None
    goal: str | None = None
    plan: str | None = None
    plan_risks: tuple[str, ...] = ()
    plan_digest: str | None = None
    plan_error: str | None = None
    review_findings: tuple[str, ...] = ()
    review_error: str | None = None
    retry_of: str | None = None
    fix_rounds_used: int = 0
    fix_in_flight: bool = False
    fix_frozen_sha: str | None = None


_TRANSITIONS = {
    (TaskState.PROPOSED, Event.PLAN_DONE): TaskState.PLANNED,
    (TaskState.PROPOSED, Event.PLAN_FAILED): TaskState.FAILED,
    (TaskState.PLANNED, Event.REVIEW_PASSED): TaskState.REVIEWED,
    (TaskState.PLANNED, Event.REVIEW_FAILED): TaskState.FAILED,
    (TaskState.REVIEWED, Event.APPROVED): TaskState.QUEUED,
    (TaskState.REVIEWED, Event.HUMAN_REJECTED): TaskState.FAILED,
    (TaskState.QUEUED, Event.START): TaskState.RUNNING,
    (TaskState.RUNNING, Event.SUCCEEDED): TaskState.AGENT_DONE,
    (TaskState.RUNNING, Event.FAILED): TaskState.FAILED,
    (TaskState.AGENT_DONE, Event.GATE_PASSED): TaskState.VERIFIED,
    (TaskState.AGENT_DONE, Event.GATE_FAILED): TaskState.FAILED,
    (TaskState.VERIFIED, Event.AUDIT_PASSED): TaskState.AUDITED,
    (TaskState.VERIFIED, Event.AUDIT_FAILED): TaskState.FAILED,
    (TaskState.AUDITED, Event.MERGED): TaskState.MERGED,
    **{(state, Event.ABANDONED): TaskState.FAILED for state in (
        TaskState.PROPOSED, TaskState.PLANNED, TaskState.REVIEWED, TaskState.QUEUED,
        TaskState.AGENT_DONE, TaskState.VERIFIED, TaskState.AUDITED)},
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
