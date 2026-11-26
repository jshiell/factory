import asyncio
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import asdict, dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Mount, Route

from factory import service
from factory.answer_questions import AnswerRejected, RoundChanged, answer_questions
from factory.audit_corpus import CASES as EVAL_CASES
from factory.audit_eval import (
    ConfigChanged,
    CorpusChanged,
    RunsChanged,
    load_meta,
    run_eval,
)
from factory.audit_gaps import gaps_to_carry
from factory.audit_report import exit_code as eval_exit_code
from factory.audit_report import load_records, render
from factory.audit_task import AUDIT_CONFIG
from factory.clean_tasks import clean_candidates, clean_tasks, remove_orphan_caches
from factory.cli import _new_eval_dir
from factory.digest import plan_digest
from factory.gate_cache import orphan_caches
from factory.ids import InvalidTaskId
from factory.job_runner import JobRunner
from factory.job_store import Job, JobNotFound, JobStore
from factory.lock import AlreadyRunning, state_lock
from factory.merge_task import MergeRefused, merge_preview, merge_task
from factory.process_registry import ReapUncertain, leftover_file_records
from factory.rebase_task import rebase_task
from factory.repos import RepoRegistry, UnknownRepo
from factory.retry_task import (
    RetryRefused,
    retry_with_plan,
)
from factory.review_notes import capped_findings, dropped_required_warning
from factory.sqlite_process_records import SqliteProcessRecords
from factory.stages import stage_config
from factory.store import TaskNotFound, task_to_dict
from factory.task import InvalidTransition, TaskState
from factory.textsafe import log_failure
from factory.waiver import InvalidWaiver, note_lines
from factory.web.auth import LoginCodes, Sessions, session_cookie_name
from factory.web.changes import TaskChanges
from factory.web.routes import repos_ui_routes, ui_routes
from factory.web.stopping import StopRequest
from factory.web_actions import (
    CLEAN_CHECK_FAILED,
    MERGE_CHECK_FAILED,
    CleanChanged,
    InvalidInput,
    ShownCommitChanged,
    WebActions,
)
from factory.worktrees import WorktreeManager


class AsciiJSONResponse(JSONResponse):
    """JSON with every non-ASCII character escaped, so a lone surrogate in stored text cannot break UTF-8 encoding."""

    def render(self, content: Any) -> bytes:
        return json.dumps(content, ensure_ascii=True, allow_nan=False, indent=None, separators=(",", ":")).encode("utf-8")


_UNREADABLE = (ValueError, KeyError, TypeError)  # what `store.load` raises for a corrupt row


class ApiError(Exception):
    def __init__(self, status: int, error: str, detail: str):
        self.status, self.error, self.detail = status, error, detail


def _error_response(request: Request, error: ApiError) -> AsciiJSONResponse:
    return AsciiJSONResponse({"error": error.error, "detail": error.detail}, status_code=error.status)


def _unknown_task(request: Request, error: Exception) -> AsciiJSONResponse:
    return AsciiJSONResponse({"error": "unknown task", "detail": str(error)}, status_code=404)


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


def _not_reviewed(request: Request, error: Exception) -> AsciiJSONResponse:
    return AsciiJSONResponse({"error": "wrong state", "detail": str(error)}, status_code=409)


def _required_string(body: dict, key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ApiError(422, "invalid input", f"{key} must be a non-empty string")
    return value


def _job_json(job: Job) -> dict:
    return asdict(job)


def _unknown_job(request: Request, error: Exception) -> AsciiJSONResponse:
    return AsciiJSONResponse({"error": "unknown job", "detail": str(error)}, status_code=404)


_SAFE_EVAL_NAME = re.compile(r"[A-Za-z0-9_-]+")


def _resumed_eval_dir(evals_root, name) -> Path:
    directory = evals_root / name if isinstance(name, str) and _SAFE_EVAL_NAME.fullmatch(name) else None
    if directory is None or directory.is_symlink() or not (directory / "meta.json").is_file():
        raise ApiError(422, "invalid input", "resume must name an earlier eval directory")
    return directory


@dataclass(frozen=True)
class Api:
    """The task, job and clean routes of one repo, with what the app state needs from them."""
    routes: list
    actions: WebActions
    clean_preview: object
    enqueue_clean: object


def api_routes(ctx, jobs, runner) -> Api:
    actions = WebActions(  # looked up at call time: this module is where these are wired
        ctx, runner, merge=lambda *args, **kwargs: merge_task(*args, **kwargs),
        rebase=lambda *args, **kwargs: rebase_task(*args, **kwargs),
        retry_with_plan=lambda *args, **kwargs: retry_with_plan(*args, **kwargs))
    async def create_task(request: Request) -> AsciiJSONResponse:
        body = await _json_object(request)
        goal = _required_string(body, "goal")
        try:
            task_id = actions.create(goal, body.get("grill", ctx.settings.grill))
        except InvalidInput as error:
            raise ApiError(422, "invalid input", str(error)) from None
        return AsciiJSONResponse({"id": task_id}, status_code=201)

    async def list_tasks(request: Request) -> AsciiJSONResponse:
        listing = service.list_tasks(ctx.store)
        tasks = [{"id": t.id, "state": t.state.value, "failure_reason": t.failure_reason} for t in listing.tasks]
        return AsciiJSONResponse({"tasks": tasks, "skipped": listing.skipped})

    def load_task(task_id: str):
        """The task, or the fixed 500 for a record that cannot be read; only the load is translated."""
        try:
            return service.get_task(ctx.store, task_id)
        except _UNREADABLE as error:
            log_failure("load task", error)
            raise ApiError(500, "task unreadable", "task record is unreadable") from None

    async def show_task(request: Request) -> AsciiJSONResponse:
        return AsciiJSONResponse(task_to_dict(load_task(request.path_params["task_id"])))

    async def costs(request: Request) -> AsciiJSONResponse:
        task_id = request.query_params.get("task_id")
        if task_id is not None:
            load_task(task_id)
        report = service.cost_report(ctx.store, ctx.ledger, task_id)
        return AsciiJSONResponse(report._asdict())

    def queued(job) -> AsciiJSONResponse:
        return AsciiJSONResponse({"job": _job_json(job)}, status_code=202)

    async def advance_task(request: Request) -> AsciiJSONResponse:
        task_id = load_task(request.path_params["task_id"]).id
        return queued(actions.enqueue_advance(task_id))

    async def abandon(request: Request) -> AsciiJSONResponse:
        task_id = load_task(request.path_params["task_id"]).id
        return queued(actions.enqueue_abandon(task_id))

    async def recover(request: Request) -> AsciiJSONResponse:
        task_id = load_task(request.path_params["task_id"]).id
        return queued(actions.enqueue_recover(task_id))

    async def rebase(request: Request) -> AsciiJSONResponse:
        task_id = load_task(request.path_params["task_id"]).id
        return queued(actions.enqueue_rebase(task_id))

    async def approval_preview(request: Request) -> AsciiJSONResponse:
        task = load_task(request.path_params["task_id"])
        if task.state is not TaskState.REVIEWED:
            raise service.NotReviewed(task)
        shown = capped_findings(task.review_findings)
        return AsciiJSONResponse({
            "goal": task.goal, "plan": task.plan, "risks": list(task.plan_risks), "review_findings": list(shown),
            "required": note_lines(shown)[1], "plan_digest": plan_digest(task.plan),
            "warning": dropped_required_warning(task.review_findings),
        })

    async def approve(request: Request) -> AsciiJSONResponse:
        task = load_task(request.path_params["task_id"])
        body = await _json_object(request)
        shown_digest = _required_string(body, "plan_digest")
        waived = _string_list(body, "waive")
        shown_findings = _string_list(body, "review_findings") if "review_findings" in body else task.review_findings
        try:
            approved = service.approve(ctx.store, task.id, shown_digest, shown_findings, waived)
        except InvalidWaiver as error:
            raise ApiError(422, "invalid waiver", str(error)) from None
        except service.PlanChanged as error:
            raise ApiError(409, "plan changed", str(error)) from None
        return AsciiJSONResponse({"id": approved.id, "state": approved.state.value})

    async def questions(request: Request) -> AsciiJSONResponse:
        task = load_task(request.path_params["task_id"])
        if task.state is not TaskState.OPEN_QUESTIONS:
            raise ApiError(409, "wrong state", f"{task.id} is {task.state.value}, not open_questions")
        return AsciiJSONResponse({"round": task.grill_rounds, "questions": list(task.open_questions)})

    async def answer(request: Request) -> AsciiJSONResponse:
        task = load_task(request.path_params["task_id"])
        body = await _json_object(request)
        round_number = body.get("round")
        if not isinstance(round_number, int) or isinstance(round_number, bool):
            raise ApiError(422, "invalid input", "round must be an integer")
        text = _required_string(body, "answers")
        try:
            answered = answer_questions(task.id, ctx.store, round_number, text)
        except AnswerRejected as error:
            raise ApiError(422, "invalid input", str(error)) from None
        except InvalidTransition as error:
            raise ApiError(409, "wrong state", str(error)) from None
        except RoundChanged as error:
            raise ApiError(409, "round changed", str(error)) from None
        return AsciiJSONResponse({"id": answered.id, "state": answered.state.value})

    async def reject(request: Request) -> AsciiJSONResponse:
        task_id = load_task(request.path_params["task_id"]).id
        task = service.reject_plan(ctx.store, task_id)
        return AsciiJSONResponse({"id": task.id, "state": ctx.store.load(task.id).state.value})

    async def merge_preview_view(request: Request) -> AsciiJSONResponse:
        task_id = request.path_params["task_id"]
        load_task(task_id)
        try:
            preview = merge_preview(task_id, ctx.store, ctx.settings.repo, ctx.settings.main_branch)
        except (MergeRefused, InvalidTransition) as error:
            raise ApiError(409, "merge refused", str(error)) from None
        except (subprocess.CalledProcessError, OSError):
            raise ApiError(409, "merge check failed", MERGE_CHECK_FAILED) from None
        return AsciiJSONResponse({"branch": preview.branch, "audited_sha": preview.task.audited_sha,
                             "diffstat": preview.diffstat, "findings": list(preview.findings),
                             "tool_config": list(preview.tool_config)})

    async def merge(request: Request) -> AsciiJSONResponse:
        task_id = request.path_params["task_id"]
        load_task(task_id)
        shown_sha = _required_string(await _json_object(request), "audited_sha")
        try:
            return queued(actions.enqueue_merge(task_id, shown_sha))
        except ShownCommitChanged as error:
            raise ApiError(409, "merge refused", str(error)) from None

    def keepable_task(task_id: str):
        load_task(task_id)
        try:
            return actions.keepable_plan(task_id)
        except RetryRefused as error:
            raise ApiError(409, "retry refused", str(error)) from None

    async def retry_preview(request: Request) -> AsciiJSONResponse:
        task = keepable_task(request.path_params["task_id"])
        shown = capped_findings(task.review_findings)
        return AsciiJSONResponse({
            "state": task.state.value, "goal": task.goal, "plan": task.plan, "risks": list(task.plan_risks),
            "review_findings": list(shown), "required": note_lines(shown)[1], "plan_digest": plan_digest(task.plan),
            "waived_findings": list(task.waived_findings), "audit_gaps": list(gaps_to_carry(task)),
            "failure_reason": task.failure_reason, "audit_findings": list(task.audit_findings),
            "warning": dropped_required_warning(task.review_findings),
        })

    async def retry(request: Request) -> AsciiJSONResponse:
        task_id = request.path_params["task_id"]
        load_task(task_id)
        body = await _json_object(request) if await request.body() else {}
        try:
            if body.get("keep_plan"):
                waive, reinstate = _string_list(body, "waive"), _string_list(body, "reinstate")
                new_id = actions.retry(task_id, _required_string(body, "plan_digest"), waive, reinstate)
            else:
                new_id = actions.retry(task_id)
        except InvalidWaiver as error:
            raise ApiError(422, "invalid waiver", str(error)) from None
        except RetryRefused as error:
            raise ApiError(409, "retry refused", str(error)) from None
        return AsciiJSONResponse({"id": new_id}, status_code=201)

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

    async def clean_preview(request: Request) -> AsciiJSONResponse:
        try:
            return AsciiJSONResponse(clean_preview_now())
        except (subprocess.CalledProcessError, OSError):
            raise ApiError(409, "clean refused", CLEAN_CHECK_FAILED) from None

    def clean_preview_now() -> dict:
        return clean_view(*clean_scan())

    def enqueue_clean(shown_token: str):
        """Queue the clean of what the caller was shown; raises `CleanChanged` if that is no longer what would be cleaned."""
        if clean_preview_now()["token"] != shown_token:
            raise CleanChanged("what would be cleaned changed since the preview")

        def clean_now():
            settings = ctx.settings
            candidates, scan = clean_scan()
            if clean_view(candidates, scan)["token"] != shown_token:
                raise RuntimeError("clean refused: what would be cleaned changed since the preview")
            worktrees = WorktreeManager(settings.repo, settings.worktree_root)
            outcomes = clean_tasks(candidates, ctx.store, worktrees, settings.repo, settings.worktree_root, settings.gate_cache_dir)
            outcomes += remove_orphan_caches(ctx.store, settings.gate_cache_dir, scan.orphans)
            return {"outcomes": [{"task_id": o.task_id, "done": o.done, "problems": o.problems} for o in outcomes]}

        return runner.submit("clean", None, clean_now)

    async def clean(request: Request) -> AsciiJSONResponse:
        shown_token = _required_string(await _json_object(request), "token")
        try:
            job = enqueue_clean(shown_token)
        except CleanChanged as error:
            raise ApiError(409, "clean refused", str(error)) from None
        except (subprocess.CalledProcessError, OSError):
            raise ApiError(409, "clean refused", CLEAN_CHECK_FAILED) from None
        return AsciiJSONResponse({"job": _job_json(job)}, status_code=202)

    async def eval_audit(request: Request) -> AsciiJSONResponse:
        body = await _json_object(request)
        runs, max_usd, only = body.get("runs", 3), body.get("max_usd", 6.0), body.get("only")
        if isinstance(runs, bool) or not isinstance(runs, int) or runs <= 0:
            raise ApiError(422, "invalid input", "runs must be a positive integer")
        if isinstance(max_usd, bool) or not isinstance(max_usd, (int, float)) or max_usd <= 0:
            raise ApiError(422, "invalid input", "max_usd must be a positive number")
        with_stage_config = body.get("stage_config", False)
        if not isinstance(with_stage_config, bool):
            raise ApiError(422, "invalid input", "stage_config must be a boolean")
        audit_config = stage_config(AUDIT_CONFIG, ctx.settings, "audit") if with_stage_config else AUDIT_CONFIG
        if only is not None:
            only = list(_string_list(body, "only"))
            unknown = [case_id for case_id in only if case_id not in {c.id for c in EVAL_CASES}]
            if unknown:
                raise ApiError(422, "invalid input", f"unknown case: {', '.join(unknown)}")
        cases = [c for c in EVAL_CASES if not only or c.id in only]
        evals_root = ctx.settings.state_dir / "evals"
        eval_dir = _resumed_eval_dir(evals_root, body["resume"]) if "resume" in body else _new_eval_dir(evals_root, ctx.clock)

        def eval_now():
            try:
                with state_lock(eval_dir):
                    ctx.reap(eval_dir)
                    try:
                        complete = run_eval(eval_dir, cases, runs, ctx.eval_runner(ctx.settings, eval_dir), max_usd,
                                          audit_config, ctx.settings.harness, ctx.settings.sandbox)
                    except CorpusChanged:
                        raise RuntimeError(f"the eval corpus changed since {eval_dir.name} started; run a fresh eval") from None
                    except ConfigChanged:
                        raise RuntimeError(f"the auditor config, plugin files, harness or sandbox differ from the ones "
                                           f"{eval_dir.name} started with; run a fresh eval") from None
                    except RunsChanged:
                        raise RuntimeError(f"resume used runs={runs} but {eval_dir.name} was started with "
                                           f"runs={load_meta(eval_dir)['runs']}; run a fresh eval") from None
            except AlreadyRunning:
                raise RuntimeError(f"an eval run already holds {eval_dir.name}; wait for it to finish") from None
            records = load_records(eval_dir)
            return {"eval_dir": str(eval_dir), "complete": complete, "exit_code": eval_exit_code(records, runs, complete, cases),
                    "report": render(records, cases)}

        job = runner.submit("eval-audit", None, eval_now)
        return AsciiJSONResponse({"job": _job_json(job), "eval_dir": str(eval_dir)}, status_code=202)

    async def list_jobs(request: Request) -> AsciiJSONResponse:
        return AsciiJSONResponse({"jobs": [_job_json(job) for job in jobs.list()]})

    async def show_job(request: Request) -> AsciiJSONResponse:
        return AsciiJSONResponse(_job_json(jobs.get(request.path_params["job_id"])))

    routes = [Route("/eval-audit", eval_audit, methods=["POST"]),
            Route("/clean-preview", clean_preview, methods=["GET"]), Route("/clean", clean, methods=["POST"]),
            Route("/tasks/{task_id}/retry-preview", retry_preview, methods=["GET"]),
            Route("/tasks/{task_id}/retry", retry, methods=["POST"]),
            Route("/tasks/{task_id}/merge", merge, methods=["POST"]),
            Route("/tasks/{task_id}/merge-preview", merge_preview_view, methods=["GET"]),
            Route("/tasks/{task_id}/approval", approval_preview, methods=["GET"]),
            Route("/tasks/{task_id}/approve", approve, methods=["POST"]),
            Route("/tasks/{task_id}/answer", answer, methods=["POST"]),
            Route("/tasks/{task_id}/questions", questions, methods=["GET"]),
            Route("/tasks/{task_id}/reject", reject, methods=["POST"]),
            Route("/tasks/{task_id}/rebase", rebase, methods=["POST"]),
            Route("/tasks/{task_id}/recover", recover, methods=["POST"]),
            Route("/tasks/{task_id}/abandon", abandon, methods=["POST"]),
            Route("/tasks/{task_id}/advance", advance_task, methods=["POST"]),
            Route("/jobs", list_jobs, methods=["GET"]), Route("/jobs/{job_id}", show_job, methods=["GET"]),
            Route("/costs", costs, methods=["GET"]), Route("/tasks", create_task, methods=["POST"]), Route("/tasks", list_tasks, methods=["GET"]),
            Route("/tasks/{task_id}", show_task, methods=["GET"]),]
    return Api(routes, actions, clean_preview_now, enqueue_clean)


async def _ui_login_code(request: Request) -> AsciiJSONResponse:
    code = request.app.state.login_codes.issue()
    return AsciiJSONResponse({"url": f"http://{request.headers['host']}/ui/login?code={code}"})


class _BoundRunner:
    """The one global job worker, submitting to the job store of one repo."""

    def __init__(self, runner: JobRunner, jobs: JobStore):
        self._runner, self._jobs = runner, jobs

    def submit(self, kind, task_id, run):
        return self._runner.submit(kind, task_id, run, self._jobs)


_EXCEPTION_HANDLERS = {ApiError: _error_response, TaskNotFound: _unknown_task, InvalidTaskId: _unknown_task,
                       JobNotFound: _unknown_job, service.NotReviewed: _not_reviewed}


def _lifespan(changes, stopping, runner):
    @asynccontextmanager
    async def lifespan(app):
        changes.attach(asyncio.get_running_loop())
        stopping.attach(asyncio.get_running_loop())
        runner.start()
        try:
            yield
        finally:
            runner.stop()
    return lifespan


async def _list_repos(request: Request) -> AsciiJSONResponse:
    registry = request.app.state.registry
    return AsciiJSONResponse({"repos": list(registry.started_names), "skipped": registry.skipped})


def _unknown_repo(request: Request, error: Exception) -> AsciiJSONResponse:
    return AsciiJSONResponse({"error": "unknown repo", "detail": str(error)}, status_code=404)


async def _no_such_repo_route(request: Request) -> AsciiJSONResponse:
    """Where no mounted repo matched: an unknown or skipped repo, answered in JSON like every other API error."""
    request.app.state.registry.get(request.path_params["repo"])
    raise ApiError(404, "not found", "no such route")


def _build_repos_app(registry: RepoRegistry) -> Starlette:
    """One app serving every started repo under /repos/<name>, with one job worker for all of them."""
    changes, stopping, runner = TaskChanges(), StopRequest(), JobRunner()
    mounts = [Route("/repos", _list_repos, methods=["GET"])]
    for name in registry.started_names:
        ctx = registry.get(name)
        jobs = JobStore(ctx.settings.db_path, on_change=changes.notify)
        if hasattr(ctx.store, "subscribe"):
            ctx.store.subscribe(changes.notify)
        mounts.append(Mount(f"/repos/{name}", routes=api_routes(ctx, jobs, _BoundRunner(runner, jobs)).routes))
    mounts += [*repos_ui_routes(), Route("/repos/{repo}", _no_such_repo_route), Route("/repos/{repo}/{rest:path}", _no_such_repo_route)]
    app = Starlette(lifespan=_lifespan(changes, stopping, runner), routes=mounts,
                    exception_handlers={**_EXCEPTION_HANDLERS, UnknownRepo: _unknown_repo})
    app.state.runner, app.state.changes, app.state.stopping = runner, changes, stopping
    app.state.registry = registry
    return app


def _build_app(ctx) -> Starlette:
    changes = TaskChanges()
    jobs = JobStore(ctx.settings.db_path, on_change=changes.notify)  # the worker thread writes: notify is thread-safe
    runner = JobRunner(jobs)
    stopping = StopRequest()
    if hasattr(ctx.store, "subscribe"):  # the legacy JSON store cannot announce writes; its pages update on reload
        ctx.store.subscribe(changes.notify)  # once per app, not per lifespan, so repeated lifespans do not stack

    api = api_routes(ctx, jobs, runner)

    app = Starlette(
        lifespan=_lifespan(changes, stopping, runner),
        # Bridge until increment 32: the CLI already speaks /repos/<name>, named after the checkout's project directory.
        routes=[Mount(f"/repos/{ctx.settings.repo.parent.name}", routes=api.routes), *api.routes, Route("/ui-login-codes", _ui_login_code, methods=["POST"]), *ui_routes()],
        exception_handlers=_EXCEPTION_HANDLERS,
    )
    app.state.runner = runner
    app.state.jobs = jobs
    app.state.actions = api.actions
    app.state.login_codes = LoginCodes(ctx.clock)
    app.state.sessions = Sessions()
    app.state.store = ctx.store
    app.state.ledger = ctx.ledger
    app.state.clean_preview = api.clean_preview
    app.state.enqueue_clean = api.enqueue_clean
    app.state.settings = ctx.settings
    app.state.changes = changes
    app.state.keepalive_seconds = 15
    app.state.stopping = stopping
    app.state.request_stop = stopping.request
    return app


class TokenFileUnsafe(Exception):
    pass


def load_or_create_token(directory) -> str:
    """The bearer token in `directory/token`: created once with mode 0600, refused if anyone else can read it."""
    Path(directory).mkdir(parents=True, exist_ok=True)
    path = Path(directory) / "token"
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


_HOST_HEADER = re.compile(r"(?P<name>\[::1\]|[^:\[\]]*)(:[0-9]+)?")


def _host_name(host: str) -> str:
    """The name in a `Host` header, or "" when the header is not a name with an optional ASCII-digit port."""
    match = _HOST_HEADER.fullmatch(host)
    return match["name"] if match else ""


_NOT_SIGNED_IN = HTMLResponse("<!doctype html><title>Factory</title><p>Not signed in. Run <code>factory ui</code>.</p>",
                              status_code=401)


_FORBIDDEN_PAGE = HTMLResponse("<!doctype html><title>Factory</title><p>Forbidden: not a same-origin request.</p>", status_code=403)


_READ_METHODS = {"GET", "HEAD"}


def _is_same_origin_request(method: str, headers) -> bool:
    own_origin = "http://" + headers["host"]
    # A read without Origin is a plain navigation; a write must name its origin (CSRF), so a missing one is refused.
    origin = headers.get("origin", own_origin if method in _READ_METHODS else None)
    # /ui pages send `Referrer-Policy: same-origin`, so their own form POST names its real Origin, which plain HTTP to a
    # non-loopback host needs (browsers send no Fetch Metadata there). A form POST may still carry `Origin: null`
    # (older pages, other browsers); accept that only when Fetch Metadata vouches for it. Reads keep refusing `null`. No Fetch Metadata fails closed.
    if origin == "null" and method not in _READ_METHODS:
        return headers.get("sec-fetch-site") == "same-origin"
    return origin == own_origin and headers.get("sec-fetch-site", "same-origin") in {"same-origin", "none"}


def _needs_session(path: str) -> bool:
    return path != "/ui/login" and not path.startswith("/ui/static/")


def _ui_refusal(method: str, headers, path, sessions: Sessions):
    if not _is_same_origin_request(method, headers):
        return _FORBIDDEN_PAGE
    if _needs_session(path) and not _has_valid_session(headers, sessions):
        return _NOT_SIGNED_IN
    return None


_UI_SECURITY_HEADERS = {
    "content-security-policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; "
                                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    "referrer-policy": "same-origin",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "cross-origin-resource-policy": "same-origin",
}


def _with_ui_security_headers(send):
    """A `send` that sets the UI's security headers on the response, replacing any the app chose."""
    async def send_with_headers(message):
        if message["type"] == "http.response.start":
            kept = [(name, value) for name, value in message["headers"] if name.decode("latin-1").lower() not in _UI_SECURITY_HEADERS]
            message = {**message, "headers": kept + [(name.encode(), value.encode()) for name, value in _UI_SECURITY_HEADERS.items()]}
        await send(message)
    return send_with_headers


def _is_ui_path(path: str) -> bool:
    return path == "/ui" or path.startswith("/ui/")


def _has_valid_session(headers, sessions: Sessions) -> bool:
    cookies = SimpleCookie(headers.get("cookie", ""))
    morsel = cookies.get(session_cookie_name(headers.get("host", "")))
    return morsel is not None and sessions.valid(morsel.value)


_DECIDING_HEADERS = ("host", "origin", "sec-fetch-site")
_REPEATED_HEADER = AsciiJSONResponse({"error": "forbidden", "detail": "a header must not be repeated"}, status_code=403)


class _Guard:
    """Pure ASGI gate: a loopback or allowed Host (DNS rebinding), no Origin (browsers), then the bearer token."""

    def __init__(self, app, token: str, allowed_hosts):
        self._app, self._token = app, token
        # Compared exactly: an upper-case or trailing-dot Host is not normalised, so it fails closed.
        self._accepted_hosts = frozenset(_LOOPBACK_HOSTS) | frozenset(allowed_hosts)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        names = [name.decode("latin-1").lower() for name, _ in scope["headers"]]
        headers = {name.decode("latin-1").lower(): value.decode("latin-1") for name, value in scope["headers"]}
        if _is_ui_path(scope["path"]):
            send = _with_ui_security_headers(send)
        refusal = _REPEATED_HEADER if any(names.count(name) > 1 for name in _DECIDING_HEADERS) \
            else self._refusal(scope["method"], headers, scope["path"])
        if refusal:
            await refusal(scope, receive, send)
            return
        await self._app(scope, receive, send)

    def _refusal(self, method, headers, path):
        if _host_name(headers.get("host", "")) not in self._accepted_hosts:
            return AsciiJSONResponse({"error": "forbidden", "detail": "host must be loopback or in server_allowed_hosts"},
                                status_code=403)
        if _is_ui_path(path):
            return _ui_refusal(method, headers, path, self._app.state.sessions)
        if "origin" in headers:
            return AsciiJSONResponse({"error": "forbidden", "detail": "browser requests are not accepted"}, status_code=403)
        scheme, _, presented = headers.get("authorization", "").partition(" ")
        if scheme != "Bearer" or not presented or not secrets.compare_digest(presented.encode(), self._token.encode()):
            return AsciiJSONResponse({"error": "unauthorized", "detail": "bearer token required"}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
        return None


def create_secured_app(ctx, token: str):
    """The app `factory serve` runs: every request needs the token, a loopback or allowed Host and no Origin."""
    app = _build_app(ctx)
    guarded = _Guard(app, token, ctx.settings.server_allowed_hosts)
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
            leftovers = leftover_file_records(state_dir)
            if leftovers:
                raise StartRefused(f"old process records in {leftovers[0].parent} ({', '.join(p.name for p in leftovers)}); "
                                   "run `factory migrate` first; nothing changed")
            try:
                reaped = ctx.reap(SqliteProcessRecords(ctx.settings.db_path))
            except ReapUncertain as uncertain:
                raise StartRefused(f"{uncertain}; nothing changed") from None
            JobStore(ctx.settings.db_path).interrupt_unfinished()
            yield reaped
    except AlreadyRunning:
        raise StartRefused("another factory run holds the lock") from None


@dataclass(frozen=True)
class Claimed:
    registry: RepoRegistry
    reaped: dict


@contextmanager
def claim_server(server_dir, contexts: dict, skipped: dict[str, str]):
    """Own the server directory and every repo's state dir for the whole serve. A repo that cannot be claimed
    is skipped with its reason; the others start."""
    try:
        with state_lock(server_dir), ExitStack() as stack:
            started, reaped, skipped = {}, {}, dict(skipped)
            for name, context in sorted(contexts.items()):
                try:
                    reaped[name] = stack.enter_context(claim_state_dir(context))
                except StartRefused as refused:
                    skipped[name] = str(refused)
                else:
                    started[name] = context
            yield Claimed(RepoRegistry(started, skipped), reaped)
    except AlreadyRunning:
        raise StartRefused("another factory server holds the lock; nothing changed") from None
