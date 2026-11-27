from factory.diagnose_task import diagnose_refusal
from factory.task import Task, TaskState


def test_a_task_that_has_not_failed_is_refused_with_a_plain_sentence():
    task = Task(id="t1", prompt="", state=TaskState.QUEUED)

    assert diagnose_refusal(task) == "Only a failed task can be diagnosed."


def test_a_failed_task_that_never_had_a_worktree_is_refused_with_a_plain_sentence():
    task = Task(id="t1", prompt="", state=TaskState.FAILED, worktree=None)

    assert diagnose_refusal(task) == "This task has no worktree to inspect."
