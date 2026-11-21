"""Helpers shared by the task detail page tests."""
from factory.task import Task, TaskState

UI_SECURITY_HEADERS = {
    "content-security-policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; "
                                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "cross-origin-resource-policy": "same-origin",
}


def assert_ui_security_headers(response):
    for name, value in UI_SECURITY_HEADERS.items():
        assert response.headers.get_list(name) == [value], name


def add(ctx, task_id="20260102-030405", state=TaskState.QUEUED, **fields) -> Task:
    task = Task(task_id, fields.pop("prompt", "prompt"), state=state, **fields)
    ctx.store.save(task)
    return task
