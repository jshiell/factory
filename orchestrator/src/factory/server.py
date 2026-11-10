import json
from contextlib import asynccontextmanager
from dataclasses import asdict

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from factory import service
from factory.abandon_task import AbandonRefused, abandon_task
from factory.advance import advance
from factory.job_runner import JobRunner
from factory.job_store import Job, JobNotFound, JobStore
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


def _job_json(job: Job) -> dict:
    return asdict(job)


def _unknown_job(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse({"error": "unknown job", "detail": str(error)}, status_code=404)


def _advance_result_json(result) -> dict:
    task = result.task
    return {"steps": [{"stage": step.stage, "state": step.state.value} for step in result.steps],
            "state": task.state.value, "failure_reason": task.failure_reason, "stopped": result.stopped}


def _task_result_json(task) -> dict:
    return {"state": task.state.value, "failure_reason": task.failure_reason}


def _refusal_as(prefix: str, refused: type[Exception], run):
    """Run `run`, turning the domain's refusal into the CLI's `<prefix> refused: <reason>` message."""
    def wrapped():
        try:
            return run()
        except refused as error:
            raise RuntimeError(f"{prefix} refused: {error}") from None
    return wrapped


def create_app(ctx) -> Starlette:
    jobs = JobStore(ctx.settings.db_path)
    runner = JobRunner(jobs)

    @asynccontextmanager
    async def lifespan(app):
        runner.start()
        try:
            yield
        finally:
            runner.stop()

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

    async def advance_task(request: Request) -> JSONResponse:
        task_id = request.path_params["task_id"]
        service.get_task(ctx.store, task_id)
        job = runner.submit("advance", task_id, lambda: _advance_result_json(advance(task_id, ctx.store, ctx.stages)))
        return JSONResponse({"job": _job_json(job)}, status_code=202)

    async def abandon(request: Request) -> JSONResponse:
        task_id = request.path_params["task_id"]
        service.get_task(ctx.store, task_id)
        run = _refusal_as("abandon", AbandonRefused, lambda: _task_result_json(abandon_task(task_id, ctx.store)))
        return JSONResponse({"job": _job_json(runner.submit("abandon", task_id, run))}, status_code=202)

    async def list_jobs(request: Request) -> JSONResponse:
        return JSONResponse({"jobs": [_job_json(job) for job in jobs.list()]})

    async def show_job(request: Request) -> JSONResponse:
        return JSONResponse(_job_json(jobs.get(request.path_params["job_id"])))

    app = Starlette(
        lifespan=lifespan,
        routes=[Route("/tasks/{task_id}/abandon", abandon, methods=["POST"]),
                Route("/tasks/{task_id}/advance", advance_task, methods=["POST"]),
                Route("/jobs", list_jobs, methods=["GET"]), Route("/jobs/{job_id}", show_job, methods=["GET"]),
                Route("/costs", costs, methods=["GET"]), Route("/tasks", create_task, methods=["POST"]), Route("/tasks", list_tasks, methods=["GET"]),
                Route("/tasks/{task_id}", show_task, methods=["GET"])],
        exception_handlers={ApiError: _error_response, TaskNotFound: _unknown_task, InvalidTaskId: _unknown_task,
                           JobNotFound: _unknown_job},
    )
    app.state.runner = runner
    return app
