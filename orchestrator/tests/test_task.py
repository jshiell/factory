from factory.task import Event, Task, TaskState, apply


def queued_task() -> Task:
    return Task(id="t1", prompt="do it")


def test_start_moves_queued_to_running_and_counts_attempt():
    task = apply(queued_task(), Event.START)

    assert task.state is TaskState.RUNNING
    assert task.attempts == 1
