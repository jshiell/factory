import json

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from factory import service
from factory.ids import InvalidTaskId
from factory.store import TaskNotFound, task_to_dict


class ApiError(Exception):
    def __init__(self, status: int, error: str, detail: str):
        self.status, self.error, self.detail = status, error, detail


def _error_response(request: Request, error: ApiError) -> JSONResponse:
    return JSONResponse({"error": error.error, "detail": error.detail}, status_code=error.status)


def _unknown_task(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse({"error": "unknown task", "detail": str(error)}, status_code=404)


async def _json_object(request: Request) -> dict:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ApiError(422, "invalid input", "body is not valid JSON") from None
    if not isinstance(body, dict):
        raise ApiError(422, "invalid input", "body must be a JSON object")
    return body


def _required_string(body: dict, key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ApiError(422, "invalid input", f"{key} must be a non-empty string")
    return value


def create_app(ctx) -> Starlette:
    async def create_task(request: Request) -> JSONResponse:
        goal = _required_string(await _json_object(request), "goal")
        task = service.create_task(ctx.store, ctx.clock, goal)
        return JSONResponse({"id": task.id}, status_code=201)

    async def list_tasks(request: Request) -> JSONResponse:
        listing = service.list_tasks(ctx.store)
        tasks = [{"id": t.id, "state": t.state.value, "failure_reason": t.failure_reason} for t in listing.tasks]
        return JSONResponse({"tasks": tasks, "skipped": listing.skipped})

    async def show_task(request: Request) -> JSONResponse:
        return JSONResponse(task_to_dict(service.get_task(ctx.store, request.path_params["task_id"])))

    async def costs(request: Request) -> JSONResponse:
        report = service.cost_report(ctx.store, ctx.ledger, request.query_params.get("task_id"))
        return JSONResponse(report._asdict())

    return Starlette(
        routes=[Route("/costs", costs, methods=["GET"]), Route("/tasks", create_task, methods=["POST"]), Route("/tasks", list_tasks, methods=["GET"]),
                Route("/tasks/{task_id}", show_task, methods=["GET"])],
        exception_handlers={ApiError: _error_response, TaskNotFound: _unknown_task, InvalidTaskId: _unknown_task},
    )
