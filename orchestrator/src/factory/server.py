import hashlib
import json
import os
import secrets
import re
import stat
import subprocess
from pathlib import Path
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from factory import service
from factory.digest import plan_digest
from factory.review_notes import capped_findings, dropped_required_warning
from factory.waiver import InvalidWaiver, note_lines
from factory.abandon_task import AbandonRefused, abandon_task
from factory.advance import advance
from factory.job_runner import JobRunner
from factory.rebase_task import RebaseRefused, rebase_task
from factory.recover_task import RecoverRefused, recover_task
from factory.merge_task import MergeRefused, merge_preview, merge_task
from factory.worktrees import WorktreeManager
from factory.audit_gaps import gaps_to_carry
from factory.retry_task import RetryRefused, require_keepable_plan, retry_task, retry_with_plan
from factory.clean_tasks import clean_candidates, clean_tasks, remove_orphan_caches
from factory.gate_cache import orphan_caches
from factory.audit_corpus import CASES as EVAL_CASES
from factory.audit_eval import CorpusChanged, run_eval
from factory.audit_report import exit_code as eval_exit_code, load_records, render
from factory.cli import _new_eval_dir
from factory.lock import AlreadyRunning, state_lock
from factory.process_registry import ReapUncertain
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


_SAFE_EVAL_NAME = re.compile(r"[A-Za-z0-9_-]+")


def _resumed_eval_dir(evals_root, name) -> Path:
    directory = evals_root / name if isinstance(name, str) and _SAFE_EVAL_NAME.fullmatch(name) else None
    if directory is None or directory.is_symlink() or not (directory / "meta.json").is_file():
        raise ApiError(422, "invalid input", "resume must name an earlier eval directory")
    return directory


def _build_app(ctx) -> Starlette:
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
            "warning": dropped_required_warning(task.review_findings),
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

    async def merge_preview_view(request: Request) -> JSONResponse:
        task_id = request.path_params["task_id"]
        service.get_task(ctx.store, task_id)
        try:
            preview = merge_preview(task_id, ctx.store, ctx.settings.repo)
        except (MergeRefused, InvalidTransition, subprocess.CalledProcessError) as error:
            raise ApiError(409, "merge refused", str(error)) from None
        return JSONResponse({"branch": preview.branch, "audited_sha": preview.task.audited_sha,
                             "diffstat": preview.diffstat, "findings": list(preview.findings)})

    async def merge(request: Request) -> JSONResponse:
        task = service.get_task(ctx.store, request.path_params["task_id"])
        shown_sha = _required_string(await _json_object(request), "audited_sha")
        if shown_sha != task.audited_sha:
            raise ApiError(409, "merge refused", "the commit shown is not the audited commit")
        repo = ctx.settings.repo

        def merge_now(task_id):
            worktrees = WorktreeManager(repo, ctx.settings.worktree_root)
            return merge_task(task_id, ctx.store, repo, worktrees, shown_sha=shown_sha)

        return enqueue(request, "merge", (MergeRefused, InvalidTransition, subprocess.CalledProcessError), merge_now)

    def keepable_task(task_id: str):
        task = service.get_task(ctx.store, task_id)
        try:
            require_keepable_plan(task, plan_digest(task.plan or ""))
        except RetryRefused as error:
            raise ApiError(409, "retry refused", str(error)) from None
        return task

    async def retry_preview(request: Request) -> JSONResponse:
        task = keepable_task(request.path_params["task_id"])
        shown = capped_findings(task.review_findings)
        return JSONResponse({
            "state": task.state.value, "goal": task.goal, "plan": task.plan, "risks": list(task.plan_risks),
            "review_findings": list(shown), "required": note_lines(shown)[1], "plan_digest": plan_digest(task.plan),
            "waived_findings": list(task.waived_findings), "audit_gaps": list(gaps_to_carry(task)),
            "failure_reason": task.failure_reason, "audit_findings": list(task.audit_findings),
            "warning": dropped_required_warning(task.review_findings),
        })

    async def retry(request: Request) -> JSONResponse:
        task_id = request.path_params["task_id"]
        service.get_task(ctx.store, task_id)
        body = await _json_object(request) if await request.body() else {}
        try:
            if body.get("keep_plan"):
                task = ctx.store.load(task_id)
                optional = {"waived": _string_list(body, "waive"), "shown_audit_gaps": gaps_to_carry(task),
                            "reinstated": _string_list(body, "reinstate")}
                new = retry_with_plan(
                    task_id, ctx.store, ctx.clock, _required_string(body, "plan_digest"), shown_findings=task.review_findings,
                    **{name: value for name, value in optional.items() if value})
            else:
                new = retry_task(task_id, ctx.store, ctx.clock)
        except InvalidWaiver as error:
            raise ApiError(422, "invalid waiver", str(error)) from None
        except RetryRefused as error:
            raise ApiError(409, "retry refused", str(error)) from None
        return JSONResponse({"id": new.id}, status_code=201)

    def clean_scan():
        settings = ctx.settings
        candidates = clean_candidates(ctx.store, settings.repo, settings.worktree_root, settings.gate_cache_dir)
        return candidates, orphan_caches(ctx.store, settings.gate_cache_dir)

    def clean_view(candidates, scan) -> dict:
        view = {"candidates": [{"id": c.task.id, "state": c.task.state.value, "directory_exists": c.directory_exists,
                                "dirty": c.dirty, "commits_ahead": c.commits_ahead, "branch_exists": c.branch_exists,
                                "outside_root": c.outside_root, "cache_bytes": c.cache_bytes, "branch_empty": c.branch_empty}
                               for c in candidates],
                "orphans": [{"name": o.name, "size": o.size} for o in scan.orphans], "skipped": list(scan.skipped)}
        view["token"] = hashlib.sha256(json.dumps(view, sort_keys=True).encode()).hexdigest()
        return view

    async def clean_preview(request: Request) -> JSONResponse:
        return JSONResponse(clean_view(*clean_scan()))

    async def clean(request: Request) -> JSONResponse:
        shown_token = _required_string(await _json_object(request), "token")
        if clean_view(*clean_scan())["token"] != shown_token:
            raise ApiError(409, "clean refused", "what would be cleaned changed since the preview")

        def clean_now():
            settings = ctx.settings
            candidates, scan = clean_scan()
            if clean_view(candidates, scan)["token"] != shown_token:
                raise RuntimeError("clean refused: what would be cleaned changed since the preview")
            worktrees = WorktreeManager(settings.repo, settings.worktree_root)
            outcomes = clean_tasks(candidates, ctx.store, worktrees, settings.repo, settings.worktree_root, settings.gate_cache_dir)
            outcomes += remove_orphan_caches(ctx.store, settings.gate_cache_dir, scan.orphans)
            return {"outcomes": [{"task_id": o.task_id, "done": o.done, "problems": o.problems} for o in outcomes]}

        return JSONResponse({"job": _job_json(runner.submit("clean", None, clean_now))}, status_code=202)

    async def eval_audit(request: Request) -> JSONResponse:
        body = await _json_object(request)
        runs, max_usd, only = body.get("runs", 3), body.get("max_usd", 6.0), body.get("only")
        if isinstance(runs, bool) or not isinstance(runs, int) or runs <= 0:
            raise ApiError(422, "invalid input", "runs must be a positive integer")
        if isinstance(max_usd, bool) or not isinstance(max_usd, (int, float)) or max_usd <= 0:
            raise ApiError(422, "invalid input", "max_usd must be a positive number")
        if only is not None:
            only = list(_string_list(body, "only"))
            unknown = [case_id for case_id in only if case_id not in {c.id for c in EVAL_CASES}]
            if unknown:
                raise ApiError(422, "invalid input", f"unknown case: {', '.join(unknown)}")
        cases = [c for c in EVAL_CASES if not only or c.id in only]
        evals_root = ctx.settings.state_dir / "evals"
        eval_dir = _resumed_eval_dir(evals_root, body["resume"]) if "resume" in body else _new_eval_dir(evals_root, ctx.clock)

        def eval_now():
            with state_lock(eval_dir):
                ctx.reap(eval_dir)
                try:
                    complete = run_eval(eval_dir, cases, runs, ctx.eval_runner(ctx.settings, eval_dir), max_usd)
                except CorpusChanged:
                    raise RuntimeError(f"the eval corpus changed since {eval_dir.name} started; run a fresh eval") from None
            records = load_records(eval_dir)
            return {"eval_dir": str(eval_dir), "complete": complete, "exit_code": eval_exit_code(records, runs, complete, cases),
                    "report": render(records, cases)}

        job = runner.submit("eval-audit", None, eval_now)
        return JSONResponse({"job": _job_json(job), "eval_dir": str(eval_dir)}, status_code=202)

    async def list_jobs(request: Request) -> JSONResponse:
        return JSONResponse({"jobs": [_job_json(job) for job in jobs.list()]})

    async def show_job(request: Request) -> JSONResponse:
        return JSONResponse(_job_json(jobs.get(request.path_params["job_id"])))

    app = Starlette(
        lifespan=lifespan,
        routes=[Route("/eval-audit", eval_audit, methods=["POST"]),
                Route("/clean-preview", clean_preview, methods=["GET"]), Route("/clean", clean, methods=["POST"]),
                Route("/tasks/{task_id}/retry-preview", retry_preview, methods=["GET"]),
                Route("/tasks/{task_id}/retry", retry, methods=["POST"]),
                Route("/tasks/{task_id}/merge", merge, methods=["POST"]),
                Route("/tasks/{task_id}/merge-preview", merge_preview_view, methods=["GET"]),
                Route("/tasks/{task_id}/approval", approval_preview, methods=["GET"]),
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


class TokenFileUnsafe(Exception):
    pass


def load_or_create_token(state_dir) -> str:
    """The bearer token in `state_dir/token`: created once with mode 0600, refused if anyone else can read it."""
    path = Path(state_dir) / "token"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if stat.S_IMODE(os.stat(path).st_mode) & 0o077:
            raise TokenFileUnsafe(f"{path} is readable by others; chmod 600 it or delete it") from None
        return path.read_text().strip()
    token = secrets.token_urlsafe(32)
    with os.fdopen(descriptor, "w") as token_file:
        token_file.write(token)
    return token


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def _host_name(host: str) -> str:
    if host.startswith("["):  # a bracketed IPv6 literal, with or without a port
        return host[:host.find("]") + 1]
    return host.partition(":")[0]


class _Guard:
    """Pure ASGI gate: loopback Host (DNS rebinding), no Origin (browsers), then the bearer token."""

    def __init__(self, app, token: str):
        self._app, self._token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = {name.decode("latin-1").lower(): value.decode("latin-1") for name, value in scope["headers"]}
        refusal = self._refusal(headers)
        if refusal:
            await refusal(scope, receive, send)
            return
        await self._app(scope, receive, send)

    def _refusal(self, headers):
        if _host_name(headers.get("host", "")) not in _LOOPBACK_HOSTS:
            return JSONResponse({"error": "forbidden", "detail": "host must be loopback"}, status_code=403)
        if "origin" in headers:
            return JSONResponse({"error": "forbidden", "detail": "browser requests are not accepted"}, status_code=403)
        scheme, _, presented = headers.get("authorization", "").partition(" ")
        if scheme != "Bearer" or not presented or not secrets.compare_digest(presented.encode(), self._token.encode()):
            return JSONResponse({"error": "unauthorized", "detail": "bearer token required"}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
        return None


def create_secured_app(ctx, token: str):
    """The app `factory serve` runs: every request needs the token, a loopback Host and no Origin."""
    app = _build_app(ctx)
    guarded = _Guard(app, token)
    guarded.state = app.state
    return guarded


class StartRefused(Exception):
    pass


@contextmanager
def claim_state_dir(ctx):
    """Own the state dir for the whole serve: the lock, one reap of orphaned agents, jobs a dead server left marked interrupted."""
    state_dir = ctx.settings.state_dir
    try:
        with state_lock(state_dir):
            try:
                reaped = ctx.reap(state_dir)
            except ReapUncertain as uncertain:
                raise StartRefused(f"{uncertain}; nothing changed") from None
            JobStore(ctx.settings.db_path).interrupt_unfinished()
            yield reaped
    except AlreadyRunning:
        raise StartRefused("another factory run holds the lock") from None
