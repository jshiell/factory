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
from factory.web.auth import session_cookie_name

_TEMPLATES = jinja2.Environment(loader=jinja2.FileSystemLoader(Path(__file__).parent / "templates"), autoescape=True)
_GOAL_LIMIT = 120
_HUMAN_GATE_STATES = {TaskState.OPEN_QUESTIONS, TaskState.REVIEWED, TaskState.AUDITED}


def _truncated(text: str) -> str:
    return text if len(text) <= _GOAL_LIMIT else text[:_GOAL_LIMIT] + "…"


def _task_row(task) -> dict:
    return {"id": task.id, "state": task.state.value, "needs_you": task.state in _HUMAN_GATE_STATES,
            "goal": _truncated(task.goal if task.goal is not None else task.prompt), "failure_reason": task.failure_reason}


def _tasks_context(store) -> dict:
    listing = service.list_tasks(store)
    return {"tasks": [_task_row(task) for task in sorted(listing.tasks, key=lambda task: task.id, reverse=True)],
            "skipped": listing.skipped}


def _task_detail(task) -> dict:
    return {"id": task.id, "state": task.state.value,
            "goal": task.goal if task.goal is not None else task.prompt, "failure_reason": task.failure_reason}


async def _task_page(request: Request) -> HTMLResponse:
    try:
        task = request.app.state.store.load(request.path_params["task_id"])
    except (TaskNotFound, InvalidTaskId):
        return HTMLResponse(_TEMPLATES.get_template("task_missing.html").render(), status_code=404)
    except (ValueError, KeyError, TypeError):  # the same errors `list` skips a row for
        return HTMLResponse(_TEMPLATES.get_template("task_unreadable.html").render(), status_code=500)
    return HTMLResponse(_TEMPLATES.get_template("task.html").render(task=_task_detail(task)))


def render_tasks(store) -> str:
    return _TEMPLATES.get_template("_tasks.html").render(_tasks_context(store))


async def _ui_page(request: Request) -> HTMLResponse:
    return HTMLResponse(_TEMPLATES.get_template("page.html").render(_tasks_context(request.app.state.store)))


def _tasks_event(store) -> str:
    data_lines = "".join(f"data: {line}\n" for line in render_tasks(store).splitlines())
    return f"event: tasks\n{data_lines}\n"


async def _first_of(waits, timeout: float):
    """For each wait (a coroutine), whether it finished within `timeout`; the unfinished ones are cancelled."""
    tasks = [asyncio.ensure_future(wait) for wait in waits]
    try:
        done, _ = await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    finally:  # also when the client goes away and the stream is cancelled mid-wait
        for task in tasks:
            task.cancel()
    return [task in done for task in tasks]


async def _events(request: Request) -> StreamingResponse:
    state = request.app.state
    store, changes, stopping = state.store, state.changes, state.stopping

    async def stream():
        while not stopping.is_requested():
            version = changes.version  # before rendering, so a write during the render sends another event
            yield _tasks_event(store)
            while True:
                changed, stopped = await _first_of((changes.wait_past(version), stopping.wait()), state.keepalive_seconds)
                if stopped:
                    return
                if changed:
                    break
                yield ": keepalive\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


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
            Route("/ui/login", _login, methods=["GET"]), Mount("/ui/static", StaticFiles(directory=STATIC_DIR))]
