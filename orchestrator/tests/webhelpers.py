"""Helpers shared by the task detail page tests."""
from factory.task import Task, TaskState


def add(ctx, task_id="20260102-030405", state=TaskState.QUEUED, **fields) -> Task:
    task = Task(task_id, fields.pop("prompt", "prompt"), state=state, **fields)
    ctx.store.save(task)
    return task
