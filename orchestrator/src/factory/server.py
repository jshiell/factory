import json
import subprocess
from contextlib import asynccontextmanager
from dataclasses import asdict

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from factory import service
from factory.digest import plan_digest
from factory.review_notes import capped_findings
from factory.waiver import InvalidWaiver, note_lines
from factory.abandon_task import AbandonRefused, abandon_task
from factory.advance import advance
from factory.job_runner import JobRunner
from factory.rebase_task import RebaseRefused, rebase_task
from factory.recover_task import RecoverRefused, recover_task
from factory.job_store import Job, JobNotFound, JobStore
from factory.ids import InvalidTaskId
from factory.task import InvalidTransition, TaskState
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


def _string_list(body: dict, key: str) -> tuple[str, ...]:
    value = body.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ApiError(422, "invalid input", f"{key} must be a list of strings")
    return tuple(value)


def _not_reviewed(request: Request, error: Exception) -> JSONResponse:
    return JSONResponse({"error": "wrong state", "detail": str(error)}, status_code=409)


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


def _refusal_as(prefix: str, refused: type[Exception] | tuple[type[Exception], ...], run):
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

    def enqueue(request: Request, kind: str, refused, run_for, to_json=_task_result_json) -> JSONResponse:
        """Queue `kind` for the path's task (404 now if unknown); the domain's refusal ends the job as failed."""
        task_id = request.path_params["task_id"]
        service.get_task(ctx.store, task_id)
        run = _refusal_as(kind, refused, lambda: to_json(run_for(task_id)))
        return JSONResponse({"job": _job_json(runner.submit(kind, task_id, run))}, status_code=202)

    async def abandon(request: Request) -> JSONResponse:
        return enqueue(request, "abandon", AbandonRefused, lambda task_id: abandon_task(task_id, ctx.store))

    async def recover(request: Request) -> JSONResponse:
        return enqueue(request, "recover", RecoverRefused, lambda task_id: recover_task(
            task_id, ctx.store, ctx.ledger, ctx.settings.budget_usd))

    async def rebase(request: Request) -> JSONResponse:
        return enqueue(
            request, "rebase", (RebaseRefused, InvalidTransition, subprocess.CalledProcessError),
            lambda task_id: rebase_task(task_id, ctx.store, ctx.settings.repo),
            lambda rebased: {**_task_result_json(rebased.task), "old_audited_sha": rebased.old_audited_sha})

    async def approval_preview(request: Request) -> JSONResponse:
        task = service.get_task(ctx.store, request.path_params["task_id"])
        if task.state is not TaskState.REVIEWED:
            raise service.NotReviewed(task)
        shown = capped_findings(task.review_findings)
        return JSONResponse({
            "goal": task.goal, "plan": task.plan, "risks": list(task.plan_risks), "review_findings": list(shown),
            "required": note_lines(shown)[1], "plan_digest": plan_digest(task.plan),
        })

    async def approve(request: Request) -> JSONResponse:
        task = service.get_task(ctx.store, request.path_params["task_id"])
        body = await _json_object(request)
        shown_digest = _required_string(body, "plan_digest")
        waived = _string_list(body, "waive")
        try:
            approved = service.approve(ctx.store, task.id, shown_digest, task.review_findings, waived)
        except InvalidWaiver as error:
            raise ApiError(422, "invalid waiver", str(error)) from None
        except service.PlanChanged as error:
            raise ApiError(409, "plan changed", str(error)) from None
        return JSONResponse({"id": approved.id, "state": approved.state.value})

    async def reject(request: Request) -> JSONResponse:
        task = service.reject_plan(ctx.store, request.path_params["task_id"])
        return JSONResponse({"id": task.id, "state": ctx.store.load(task.id).state.value})

    async def list_jobs(request: Request) -> JSONResponse:
        return JSONResponse({"jobs": [_job_json(job) for job in jobs.list()]})

    async def show_job(request: Request) -> JSONResponse:
        return JSONResponse(_job_json(jobs.get(request.path_params["job_id"])))

    app = Starlette(
        lifespan=lifespan,
        routes=[Route("/tasks/{task_id}/approval", approval_preview, methods=["GET"]),
                Route("/tasks/{task_id}/approve", approve, methods=["POST"]),
                Route("/tasks/{task_id}/reject", reject, methods=["POST"]),
                Route("/tasks/{task_id}/rebase", rebase, methods=["POST"]),
                Route("/tasks/{task_id}/recover", recover, methods=["POST"]),
                Route("/tasks/{task_id}/abandon", abandon, methods=["POST"]),
                Route("/tasks/{task_id}/advance", advance_task, methods=["POST"]),
                Route("/jobs", list_jobs, methods=["GET"]), Route("/jobs/{job_id}", show_job, methods=["GET"]),
                Route("/costs", costs, methods=["GET"]), Route("/tasks", create_task, methods=["POST"]), Route("/tasks", list_tasks, methods=["GET"]),
                Route("/tasks/{task_id}", show_task, methods=["GET"])],
        exception_handlers={ApiError: _error_response, TaskNotFound: _unknown_task, InvalidTaskId: _unknown_task,
                           JobNotFound: _unknown_job,
                           service.NotReviewed: _not_reviewed},
    )
    app.state.runner = runner
    return app
