from factory.task import Event, Task, TaskState, apply


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
