import asyncio
from pathlib import Path

import jinja2
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from factory import service
from factory.ids import InvalidTaskId
from factory.store import TaskNotFound
from factory.task import TaskState
from factory.textsafe import escape_control
from factory.web.auth import session_cookie_name

_TEMPLATES = jinja2.Environment(loader=jinja2.FileSystemLoader(Path(__file__).parent / "templates"), autoescape=True)
_GOAL_LIMIT = 120
_HUMAN_GATE_STATES = {TaskState.OPEN_QUESTIONS, TaskState.REVIEWED, TaskState.AUDITED}
_UNREADABLE = (ValueError, KeyError, TypeError)  # what `load` lets escape for a bad row; `list` skips a row for the same


def _truncated(text: str) -> str:
    return text if len(text) <= _GOAL_LIMIT else text[:_GOAL_LIMIT] + "…"


def _task_row(task) -> dict:
    return {"id": _safe(task.id), "state": task.state.value, "needs_you": task.state in _HUMAN_GATE_STATES,
            "goal": _safe(_truncated(task.goal if task.goal is not None else task.prompt)), "failure_reason": _safe(task.failure_reason)}


def _tasks_context(store) -> dict:
    listing = service.list_tasks(store)
    return {"tasks": [_task_row(task) for task in sorted(listing.tasks, key=lambda task: task.id, reverse=True)],
            "skipped": [_safe(name) for name in listing.skipped]}


def _safe(value):
    """Agent-written text is shown with control and invisible format characters escaped; other values pass through."""
    return escape_control(value) if isinstance(value, str) else value


def _task_detail(task) -> dict:
    facts = (("retry of", task.retry_of), ("fix rounds", task.fix_rounds_used), ("attempts", task.attempts),
             ("denials", len(task.denials)), ("worktree", task.worktree),
             ("branch", f"factory/{task.id}" if task.worktree else None), ("plan error", task.plan_error),
             ("review error", task.review_error), ("audit error", task.audit_error))
    lists = (("risks", task.plan_risks), ("review findings", task.review_findings),
             ("audit findings", task.audit_findings), ("waived findings", task.waived_findings))
    questions = [{"number": _safe(question["number"]), "title": _safe(question["title"]), "recommended": _safe(question["recommended"])}
                 for question in task.open_questions]
    return {"id": task.id, "state": task.state.value, "needs_you": task.state in _HUMAN_GATE_STATES,
            "facts": [(label, _safe(value)) for label, value in facts if value],
            "goal": _safe(task.goal if task.goal is not None else task.prompt), "failure_reason": _safe(task.failure_reason),
            "plan": _safe(task.plan), "open_questions": questions, "grill_rounds": task.grill_rounds,
            "lists": [(label, [_safe(item) for item in items]) for label, items in lists if items]}


def _load_task(request: Request):
    """The requested task, or the HTML page refusing it (missing, invalid or unreadable id), which never shows the id."""
    try:
        return request.app.state.store.load(request.path_params["task_id"]), None
    except (TaskNotFound, InvalidTaskId):
        return None, HTMLResponse(_TEMPLATES.get_template("task_missing.html").render(), status_code=404)
    except _UNREADABLE:
        return None, HTMLResponse(_TEMPLATES.get_template("task_unreadable.html").render(), status_code=500)


async def _task_page(request: Request) -> HTMLResponse:
    task, refusal = _load_task(request)
    return refusal or HTMLResponse(_TEMPLATES.get_template("task.html").render(task=_task_detail(task)))


def _not_reviewed_page() -> HTMLResponse:
    return HTMLResponse(_TEMPLATES.get_template("task_not_reviewed.html").render(), status_code=409)


async def _reject_page(request: Request) -> HTMLResponse:
    task, refusal = _load_task(request)
    if refusal:
        return refusal
    if task.state is not TaskState.REVIEWED:
        return _not_reviewed_page()
    return HTMLResponse(_TEMPLATES.get_template("reject.html").render(task=_task_detail(task)))


def _render_task(task) -> str:
    return _TEMPLATES.get_template("_task.html").render(task=_task_detail(task))


def render_tasks(store) -> str:
    return _TEMPLATES.get_template("_tasks.html").render(_tasks_context(store))


async def _ui_page(request: Request) -> HTMLResponse:
    return HTMLResponse(_TEMPLATES.get_template("page.html").render(_tasks_context(request.app.state.store)))


def _event(name: str, fragment: str) -> str:
    """One SSE event; splitting on every line break keeps a CR in the text from ending a line or the event."""
    data_lines = "".join(f"data: {line}\n" for line in fragment.splitlines())
    return f"event: {name}\n{data_lines}\n"


async def _first_of(waits, timeout: float):
    """For each wait (a coroutine), whether it finished within `timeout`; the unfinished ones are cancelled."""
    tasks = [asyncio.ensure_future(wait) for wait in waits]
    try:
        done, _ = await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    finally:  # also when the client goes away and the stream is cancelled mid-wait
        for task in tasks:
            task.cancel()
    return [task in done for task in tasks]


def _stream(request: Request, render) -> StreamingResponse:
    """Send `render()` (an SSE event, or None for nothing new) now and after every store change, with keepalives
    between, until a stop."""
    state = request.app.state
    changes, stopping = state.changes, state.stopping

    async def stream():
        while not stopping.is_requested():
            version = changes.version  # before rendering, so a write during the render sends another event
            event = render()
            if event is not None:
                yield event
            while True:
                changed, stopped = await _first_of((changes.wait_past(version), stopping.wait()), state.keepalive_seconds)
                if stopped:
                    return
                if changed:
                    break
                yield ": keepalive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


async def _events(request: Request) -> StreamingResponse:
    store = request.app.state.store
    return _stream(request, lambda: _event("tasks", render_tasks(store)))


async def _task_events(request: Request) -> StreamingResponse | HTMLResponse:
    _, refusal = _load_task(request)  # before the stream exists, so a refusal still has its real status
    if refusal:
        return refusal
    store, task_id = request.app.state.store, request.path_params["task_id"]
    return _stream(request, _unless_unchanged(lambda: _event("task", _task_fragment(store, task_id))))


def _task_fragment(store, task_id: str) -> str:
    """The task fragment, or the unreadable notice when a later write left the row unreadable; never the raw row."""
    try:
        return _render_task(store.load(task_id))
    except _UNREADABLE:
        return _TEMPLATES.get_template("_task_unreadable.html").render()


def _unless_unchanged(render):
    """`render`, but None when it gives what it gave last time: any store write wakes the stream, and resending
    an unchanged fragment would reset the reader's selection and scroll."""
    last = None

    def render_if_changed():
        nonlocal last
        event = render()
        if event == last:
            return None
        last = event
        return event

    return render_if_changed


async def _ui_without_slash(request: Request) -> RedirectResponse:
    return RedirectResponse("/ui/", status_code=308)


async def _login(request: Request) -> HTMLResponse:
    state = request.app.state
    if not state.login_codes.redeem(request.query_params.get("code", "")):
        return HTMLResponse("<!doctype html><title>factory</title><p>that login link is used or expired; run <code>factory ui</code></p>",
                            status_code=401)
    response = HTMLResponse('<!doctype html><title>factory</title><p><a href="/ui/">open the factory UI</a></p>')
    response.set_cookie(session_cookie_name(request.headers.get("host", "")), state.sessions.create(),
                        httponly=True, samesite="strict", path="/ui")
    return response


STATIC_DIR = Path(__file__).parent / "static"


def ui_routes() -> list[Route | Mount]:
    return [Route("/ui", _ui_without_slash, methods=["GET"]), Route("/ui/", _ui_page, methods=["GET"]),
            Route("/ui/events", _events, methods=["GET"]), Route("/ui/tasks/{task_id}", _task_page, methods=["GET"]),
            Route("/ui/tasks/{task_id}/reject", _reject_page, methods=["GET"]),
            Route("/ui/tasks/{task_id}/events", _task_events, methods=["GET"]),
            Route("/ui/login", _login, methods=["GET"]), Mount("/ui/static", StaticFiles(directory=STATIC_DIR))]
