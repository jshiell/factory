from pathlib import Path

from factory.task import Task, TaskState


def diagnose_refusal(task: Task) -> str | None:
    """A plain sentence saying why the task cannot be diagnosed, or None when it can."""
    if task.state is not TaskState.FAILED:
        return "Only a failed task can be diagnosed."
    if task.worktree is None:
        return "This task has no worktree to inspect."
    if not Path(task.worktree).is_dir():
        return "This task's worktree no longer exists (it may have been cleaned)."
    return None
