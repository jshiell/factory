import pytest

from factory.task import Event, InvalidTransition, Task, TaskState, apply


def queued_task() -> Task:
    return Task(id="t1", prompt="do it")


def test_start_moves_queued_to_running_and_counts_attempt():
    task = apply(queued_task(), Event.START)

    assert task.state is TaskState.RUNNING
    assert task.attempts == 1


def test_succeeded_moves_running_to_agent_done():
    running = apply(queued_task(), Event.START)

    task = apply(running, Event.SUCCEEDED)

    assert task.state is TaskState.AGENT_DONE
    assert task.attempts == 1


def test_failed_moves_running_to_failed_with_reason():
    running = apply(queued_task(), Event.START)

    task = apply(running, Event.FAILED, reason="budget exhausted")

    assert task.state is TaskState.FAILED
    assert task.failure_reason == "budget exhausted"


@pytest.mark.parametrize("event", [Event.SUCCEEDED, Event.FAILED])
def test_queued_task_cannot_finish_before_starting(event):
    with pytest.raises(InvalidTransition):
        apply(queued_task(), event)


def test_terminal_task_cannot_start_again():
    failed = apply(apply(queued_task(), Event.START), Event.FAILED, reason="x")

    with pytest.raises(InvalidTransition):
        apply(failed, Event.START)


def agent_done_task() -> Task:
    return apply(apply(queued_task(), Event.START), Event.SUCCEEDED)


def test_gate_passed_moves_agent_done_to_verified():
    assert apply(agent_done_task(), Event.GATE_PASSED).state is TaskState.VERIFIED


def test_gate_failed_moves_agent_done_to_failed_with_reason():
    task = apply(agent_done_task(), Event.GATE_FAILED, reason="tests_failed")

    assert task.state is TaskState.FAILED
    assert task.failure_reason == "tests_failed"


@pytest.mark.parametrize("event", [Event.GATE_PASSED, Event.GATE_FAILED])
def test_gate_events_are_invalid_before_the_agent_is_done(event):
    with pytest.raises(InvalidTransition):
        apply(apply(queued_task(), Event.START), event)


def verified_task() -> Task:
    return Task(id="t1", prompt="do it", state=TaskState.VERIFIED)


def test_audit_passed_moves_verified_to_audited():
    task = apply(verified_task(), Event.AUDIT_PASSED)

    assert task.state is TaskState.AUDITED
    assert task.failure_reason is None


def test_audit_failed_moves_verified_to_failed_with_reason():
    task = apply(verified_task(), Event.AUDIT_FAILED, "audit_rejected")

    assert task.state is TaskState.FAILED
    assert task.failure_reason == "audit_rejected"


@pytest.mark.parametrize("event", [Event.AUDIT_PASSED, Event.AUDIT_FAILED])
@pytest.mark.parametrize("state", [TaskState.QUEUED, TaskState.RUNNING, TaskState.AGENT_DONE])
def test_audit_events_are_invalid_before_verified(event, state):
    with pytest.raises(InvalidTransition):
        apply(Task(id="t1", prompt="p", state=state), event)


def task_in(state: TaskState) -> Task:
    return Task(id="t1", prompt="", state=state)


FRONT_HALF = [
    (TaskState.PROPOSED, Event.PLAN_DONE, TaskState.PLANNED, None),
    (TaskState.PROPOSED, Event.PLAN_FAILED, TaskState.FAILED, "plan_modified_worktree"),
    (TaskState.PLANNED, Event.REVIEW_PASSED, TaskState.REVIEWED, None),
    (TaskState.PLANNED, Event.REVIEW_FAILED, TaskState.FAILED, "plan_rejected"),
    (TaskState.REVIEWED, Event.APPROVED, TaskState.QUEUED, None),
    (TaskState.REVIEWED, Event.HUMAN_REJECTED, TaskState.FAILED, "plan_rejected_by_human"),
]


@pytest.mark.parametrize("source,event,target,reason", FRONT_HALF)
def test_front_half_transitions(source, event, target, reason):
    task = apply(task_in(source), event, reason)

    assert task.state is target
    assert task.failure_reason == reason


@pytest.mark.parametrize("source,event,target,reason", FRONT_HALF)
@pytest.mark.parametrize("state", list(TaskState))
def test_front_half_events_are_rejected_from_every_other_state(source, event, target, reason, state):
    if state is source:
        return
    with pytest.raises(InvalidTransition):
        apply(task_in(state), event, reason)


@pytest.mark.parametrize("state", [TaskState.PROPOSED, TaskState.PLANNED, TaskState.REVIEWED])
def test_start_is_rejected_before_the_plan_is_approved(state):
    with pytest.raises(InvalidTransition):
        apply(task_in(state), Event.START)
