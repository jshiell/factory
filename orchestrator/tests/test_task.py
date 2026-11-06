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
