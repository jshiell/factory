import asyncio
import sqlite3
import subprocess
from collections.abc import Callable
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, quote

import jinja2
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from factory import answer_questions, approve_plan, service
from factory.abandon_task import can_abandon
from factory.advance import can_advance
from factory.audit_gaps import gaps_to_carry
from factory.digest import plan_digest
from factory.failure_codes import explain
from factory.ids import InvalidTaskId
from factory.merge_task import MergeRefused, can_merge
from factory.rebase_task import can_rebase
from factory.recover_task import can_recover
from factory.retry_task import RetryRefused, can_retry
from factory.review_notes import capped_findings, dropped_required_warning
from factory.store import TaskNotFound
from factory.task import InvalidTransition, Task, TaskState
from factory.textsafe import escape_control
from factory.waiver import InvalidWaiver, note_lines
from factory.web.auth import session_cookie_name
from factory.web.markdown import render_markdown, render_markdown_with_urls
from factory.web_actions import (
    CLEAN_CHECK_FAILED,
    MERGE_CHECK_FAILED,
    CleanChanged,
    InvalidInput,
    ShownCommitChanged,
    WebActions,
)

_TEMPLATES = jinja2.Environment(loader=jinja2.FileSystemLoader(Path(__file__).parent / "templates"), autoescape=True)
_TEMPLATES.filters["markdown"] = render_markdown
_TEMPLATES.filters["markdown_with_urls"] = render_markdown_with_urls
_TEMPLATES.filters["humanise"] = lambda value: value[:1].upper() + value[1:].replace("_", " ")  # not capitalize: it lowercases the rest
# One label per task field, shared by the fact lines and by the stopped description of a stage error.
_FIELD_LABELS = {"retry_of": "Retry of", "fix_rounds": "Fix rounds", "attempts": "Attempts", "denials": "Denials",
                 "worktree": "Worktree", "branch": "Branch", "plan_error": "Plan error", "review_error": "Review error",
                 "audit_error": "Audit error", "failure_reason": "Failure"}
_GOAL_LIMIT = 120
_ERROR_LIMIT = 500
_HUMAN_GATE_STATES = {TaskState.OPEN_QUESTIONS, TaskState.REVIEWED, TaskState.AUDITED}
_UNREADABLE = (ValueError, KeyError, TypeError)  # what `load` lets escape for a bad row; `list` skips a row for the same


def _truncated(text: str, limit: int = _GOAL_LIMIT) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _task_row(task) -> dict:
    return {"id": _safe(task.id), "state": task.state.value, "needs_you": task.state in _HUMAN_GATE_STATES,
            "goal": _safe(_truncated(task.goal if task.goal is not None else task.prompt)), "failure_reason": _safe(_described("failure_reason", task.failure_reason))}


def _tasks_context(store) -> dict:
    listing = service.list_tasks(store)
    return {"tasks": [_task_row(task) for task in sorted(listing.tasks, key=lambda task: task.id, reverse=True)],
            "skipped": [_safe(name) for name in listing.skipped]}


def _safe(value):
    """Agent-written text is shown with control and invisible format characters escaped; other values pass through."""
    return escape_control(value) if isinstance(value, str) else value


def _explanation(field: str, code: str | None) -> str | None:
    """Our own description of a known failure code; None for free text, which is shown as it is."""
    explanation = explain(field, code) if code else None
    return explanation.text if explanation else None


def _described(field: str, code: str | None) -> str | None:
    """A stage error as 'description (code)' when the code is known; free text is shown as it is."""
    description = _explanation(field, code)
    return f"{description} ({code})" if description else code


def _task_detail(task) -> dict:
    facts = {"retry_of": task.retry_of, "fix_rounds": task.fix_rounds_used, "attempts": task.attempts,
             "denials": len(task.denials), "worktree": task.worktree,
             "branch": f"factory/{task.id}" if task.worktree else None,
             "plan_error": _described("plan_error", task.plan_error),
             "review_error": _described("review_error", task.review_error),
             "audit_error": _described("audit_error", task.audit_error)}
    lists = (("Risks", task.plan_risks), ("Review findings", task.review_findings),
             ("Audit findings", task.audit_findings), ("Waived findings", task.waived_findings))
    questions = [{"number": _safe(question["number"]), "title": _safe(question["title"]), "recommended": _safe(question["recommended"])}
                 for question in task.open_questions]
    return {"id": task.id, "state": task.state.value, "needs_you": task.state in _HUMAN_GATE_STATES,
            "facts": [(_FIELD_LABELS[field], _safe(value)) for field, value in facts.items() if value],
            "goal": _safe(task.goal if task.goal is not None else task.prompt), "failure_reason": _safe(task.failure_reason),
            "failure_explanation": _explanation("failure_reason", task.failure_reason),
            "plan":_safe(task.plan), "open_questions": questions, "grill_rounds": task.grill_rounds,
            "lists": [(label, [_safe(item) for item in items]) for label, items in lists if items]}


def _job_view(job) -> dict | None:
    """What the task panel shows of a job: its error is job-failure text, so it is capped and escaped."""
    if job is None:
        return None
    result = job.result or {}
    stopped_error = result.get("stopped_error") or {}
    field, code = stopped_error.get("field"), stopped_error.get("code")
    description = _described(field, code) if field and code and _explanation(field, code) else None
    return {"kind": _safe(job.kind), "state": job.state,
            "error": _safe(_truncated(job.error, _ERROR_LIMIT)) if job.error else None, "stopped": _safe(result.get("stopped")),
            "stopped_description": f"{_FIELD_LABELS[field]}: {description}" if description else None}


_ACTION_LINKS = (("advance", "Advance this task", can_advance), ("rebase", "Rebase this task", can_rebase),
                 ("merge", "Merge this task", can_merge), ("recover", "Recover this task", can_recover),
                 ("abandon", "Abandon this task", can_abandon))


def _offered_actions(task, job) -> list[tuple[str, str]]:
    """The (name, text) links the task page offers: those valid for its state, none while its job is unfinished
    (the POST would be refused), and retry whenever it is valid (a retry creates a new task)."""
    busy = job is not None and job.state in ("queued", "running")
    links = [] if busy else [(name, text) for name, text, allowed in _ACTION_LINKS if allowed(task)]
    if can_retry(task):
        links.append(("retry", "Retry this task"))
    return links


async def _costs_page(request: Request) -> HTMLResponse:
    report = service.cost_report(request.app.state.store, request.app.state.ledger)
    shown = {"total": report.total, "assumed": report.assumed,
             "by_stage": [(_safe(stage), cost) for stage, cost in sorted(report.by_stage.items())],
             "by_task": [(_safe(task_id), cost) for task_id, cost in sorted(report.by_task.items())]}
    return HTMLResponse(_TEMPLATES.get_template("costs.html").render(report=shown))


def _clean_flags(candidate: dict) -> list[str]:
    flags = [("directory missing", not candidate["directory_exists"]), ("dirty", candidate["dirty"]),
             (f"{candidate['commits_ahead']} commits ahead", candidate["commits_ahead"]),
             ("branch exists", candidate["branch_exists"]), ("outside the worktree root", candidate["outside_root"]),
             (f"gate cache {candidate['cache_bytes']} bytes", candidate["cache_bytes"] is not None)]
    return [text for text, present in flags if present]


def _clean_git_failed_page() -> HTMLResponse:
    return _wrong_state_page(CLEAN_CHECK_FAILED)


async def _clean_page(request: Request) -> HTMLResponse:
    try:
        view = request.app.state.clean_preview()
    except (subprocess.CalledProcessError, OSError):  # the text is a git command line or a path
        return _clean_git_failed_page()
    shown = {"token": view["token"], "skipped": [_safe(name) for name in view["skipped"]],
             "orphans": [{"name": _safe(o["name"]), "size": o["size"]} for o in view["orphans"]],
             "candidates": [{"id": _safe(c["id"]), "state": c["state"], "flags": _clean_flags(c)}
                            for c in view["candidates"]]}
    return HTMLResponse(_TEMPLATES.get_template("clean.html").render(view=shown))


async def _clean(request: Request) -> RedirectResponse | HTMLResponse:
    try:
        token = _single(await _read_form(request), "token")
    except _BadForm:
        return _bad_form_page()
    if token is None:
        return _bad_form_page()
    try:
        request.app.state.enqueue_clean(token)
    except CleanChanged:
        return _wrong_state_page("What would be cleaned changed since you opened the page. Open it again to see "
                                 "what would be cleaned now.")
    except (subprocess.CalledProcessError, OSError):
        return _clean_git_failed_page()
    return RedirectResponse("/ui/jobs", status_code=303)


_JOBS_SHOWN = 50


async def _jobs_page(request: Request) -> HTMLResponse:
    newest = request.app.state.jobs.list()[-_JOBS_SHOWN:][::-1]
    shown = [{"kind": _safe(job.kind), "state": job.state, "task_id": _safe(job.task_id),
              "error": _safe(_truncated(job.error, _ERROR_LIMIT)) if job.error else None} for job in newest]
    return HTMLResponse(_TEMPLATES.get_template("jobs.html").render(jobs=shown))


def _missing_page() -> HTMLResponse:
    return HTMLResponse(_TEMPLATES.get_template("task_missing.html").render(), status_code=404)


def _unreadable_page() -> HTMLResponse:
    return HTMLResponse(_TEMPLATES.get_template("task_unreadable.html").render(), status_code=500)


def _repo(request: Request):
    """The stores and actions of the repo the request is for; the single-repo app keeps them on the app state."""
    state = request.app.state
    name = request.path_params.get("repo")
    if name is None:
        return state
    state.registry.get(name)  # an unknown or skipped repo raises UnknownRepo
    return state.repos[name]


def _base(request: Request) -> str:
    repo = request.path_params.get("repo")
    return "/ui" if repo is None else f"/ui/repos/{quote(repo, safe='')}"


def _load_task(request: Request):
    """The requested task, or the HTML page refusing it (missing, invalid or unreadable id), which never shows the id."""
    try:
        return _repo(request).store.load(request.path_params["task_id"]), None
    except (TaskNotFound, InvalidTaskId):
        return None, _missing_page()
    except _UNREADABLE:
        return None, _unreadable_page()


async def _task_page(request: Request) -> HTMLResponse:
    task, refusal = _load_task(request)
    if refusal:
        return refusal
    job = _repo(request).jobs.latest_for_task(task.id)
    return HTMLResponse(_TEMPLATES.get_template("task.html").render(
        task=_task_detail(task), job=_job_view(job), actions=_offered_actions(task, job), base=_base(request)))


def _not_reviewed_page() -> HTMLResponse:
    return HTMLResponse(_TEMPLATES.get_template("task_not_reviewed.html").render(), status_code=409)


async def _reject_page(request: Request) -> HTMLResponse:
    task, refusal = _load_task(request)
    if refusal:
        return refusal
    if task.state is not TaskState.REVIEWED:
        return _not_reviewed_page()
    return HTMLResponse(_TEMPLATES.get_template("reject.html").render(task=_task_detail(task), base=_base(request)))


async def _reject(request: Request) -> RedirectResponse | HTMLResponse:
    task, refusal = _load_task(request)
    if refusal:
        return refusal
    try:
        approve_plan.reject_plan(task.id, _repo(request).store)
    except InvalidTransition:
        return _not_reviewed_page()
    except (TaskNotFound, InvalidTaskId):  # the task went between the load above and the one inside reject_plan
        return _missing_page()
    except _UNREADABLE:
        return _unreadable_page()
    return RedirectResponse(f"{_base(request)}/tasks/{quote(task.id, safe='')}", status_code=303)


def _default_answer(question) -> str:
    """The recommended answer as one line: the prefilled value and what a blank answer means."""
    return " ".join(escape_control(question["recommended"]).split())


def _answer_page_detail(task) -> dict:
    questions = [{"number": _safe(question["number"]), "title": _safe(question["title"]), "body": _safe(question.get("body", "")),
                  "default": _default_answer(question)} for question in task.open_questions]
    return {"id": task.id, "goal": _safe(task.goal if task.goal is not None else task.prompt),
            "grill_rounds": task.grill_rounds, "open_questions": questions}


def _wrong_state_page(message: str) -> HTMLResponse:
    return HTMLResponse(_TEMPLATES.get_template("task_wrong_state.html").render(message=message), status_code=409)


def _no_open_questions_page() -> HTMLResponse:
    return _wrong_state_page("That task has no open questions to answer.")


async def _answer_page(request: Request) -> HTMLResponse:
    task, refusal = _load_task(request)
    if refusal:
        return refusal
    if task.state is not TaskState.OPEN_QUESTIONS:
        return _no_open_questions_page()
    return HTMLResponse(_TEMPLATES.get_template("answer.html").render(task=_answer_page_detail(task), base=_base(request)))


# Above 3 x MAX_ANSWER_BYTES, as percent-encoding triples each non-ASCII byte, so the answer-size 422 stays reachable.
_FORM_CAP_BYTES = 64 * 1024
_MAX_FORM_FIELDS = 32  # a round and at most 10 answers, with room to spare
_FORM_TYPE = "application/x-www-form-urlencoded"


class _BadForm(Exception):
    """The posted form is not one the UI's own pages send."""


def _bad_form_page() -> HTMLResponse:
    return HTMLResponse(_TEMPLATES.get_template("form_rejected.html").render(), status_code=422)


async def _read_capped_body(request: Request) -> bytes:
    """The body, refused as soon as it passes the cap: Content-Length is not trusted and the rest is never read."""
    chunks, size = [], 0
    async with aclosing(request.stream()) as stream:
        async for chunk in stream:
            size += len(chunk)
            if size > _FORM_CAP_BYTES:
                raise _BadForm
            chunks.append(chunk)
    return b"".join(chunks)


async def _read_form(request: Request) -> dict[str, list[str]]:
    """The urlencoded form, every value of every field; anything a browser would not send from the UI's pages is a _BadForm."""
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != _FORM_TYPE:
        raise _BadForm
    body = await _read_capped_body(request)
    try:  # UnicodeDecodeError and parse_qs's too-many-fields error are both ValueErrors
        return parse_qs(body.decode("ascii"), keep_blank_values=True, encoding="utf-8", errors="replace",
                        max_num_fields=_MAX_FORM_FIELDS)
    except ValueError:
        raise _BadForm from None


def _single(form: dict[str, list[str]], name: str) -> str | None:
    """The field's one value, None when absent; a repeated field is ambiguous, so it is refused."""
    values = form.get(name)
    if values is not None and len(values) != 1:
        raise _BadForm
    return values[0] if values else None


def _round(form: dict[str, list[str]]) -> int:
    value = _single(form, "round")
    # isdigit alone admits superscript and fullwidth digits; int() alone admits ' 2', '+2' and '0_2', and refuses past 4300 digits with a ValueError.
    if value is None or not (value.isascii() and value.isdigit()):
        raise _BadForm
    try:
        return int(value)
    except ValueError:
        raise _BadForm from None


def _answer_text(task, form: dict[str, list[str]]) -> str:
    """One `N. text` line per stored question, in order; a blank or missing field takes the default."""
    lines = []
    for index, question in enumerate(task.open_questions):
        typed = " ".join((_single(form, f"q{index}") or "").split())
        lines.append(f"{question['number']}. {typed or _default_answer(question)}")
    return "\n".join(lines)


async def _answer(request: Request) -> RedirectResponse | HTMLResponse:
    task, refusal = _load_task(request)
    if refusal:
        return refusal
    if task.state is not TaskState.OPEN_QUESTIONS:  # before the body, so a wrong-state task posted a bad form gets the 409
        return _no_open_questions_page()
    try:
        form = await _read_form(request)
        answer_questions.answer_questions(task.id, _repo(request).store, _round(form), _answer_text(task, form))
    except (_BadForm, answer_questions.AnswerRejected):  # AnswerRejected is a ValueError: keep it ahead of any _UNREADABLE
        return _bad_form_page()
    except answer_questions.RoundChanged:
        return _wrong_state_page("The questions changed since you opened this page. Open the task again to see the new ones.")
    except InvalidTransition:  # the task left open_questions between the load above and the one inside answer_questions
        return _no_open_questions_page()
    except (TaskNotFound, InvalidTaskId):  # the task went between the load above and the one inside answer_questions
        return _missing_page()
    except _UNREADABLE:
        return _unreadable_page()
    return RedirectResponse(f"{_base(request)}/tasks/{quote(task.id, safe='')}", status_code=303)


def _approve_page_detail(task) -> dict:
    """What the CLI preview shows: the capped findings with required notes numbered, and the dropped-required warning."""
    lines, _ = note_lines(capped_findings(task.review_findings))
    return {"id": task.id, "goal": _safe(task.goal if task.goal is not None else task.prompt), "plan": _safe(task.plan),
            "risks": [_safe(risk) for risk in task.plan_risks], "findings": _safe("\n".join(lines)),
            "warning": _safe(dropped_required_warning(task.review_findings)), "plan_digest": plan_digest(task.plan)}


def _not_reviewed_wrong_state_page() -> HTMLResponse:
    return _wrong_state_page("That task is not waiting for a plan review, so its plan cannot be approved.")


async def _approve_page(request: Request) -> HTMLResponse:
    task, refusal = _load_task(request)
    if refusal:
        return refusal
    if task.state is not TaskState.REVIEWED:
        return _not_reviewed_wrong_state_page()
    return HTMLResponse(_TEMPLATES.get_template("approve.html").render(task=_approve_page_detail(task), base=_base(request)))


def _shown_digest(form: dict[str, list[str]]) -> str:
    value = _single(form, "plan_digest")
    if not value:
        raise _BadForm
    return value


async def _approve(request: Request) -> RedirectResponse | HTMLResponse:
    task, refusal = _load_task(request)
    if refusal:
        return refusal
    if task.state is not TaskState.REVIEWED:  # before the body, so a wrong-state task posted a bad form gets the 409
        return _not_reviewed_wrong_state_page()
    try:
        form = await _read_form(request)
        shown_digest = _shown_digest(form)
    except _BadForm:
        return _bad_form_page()
    try:
        service.approve(_repo(request).store, task.id, shown_digest, task.review_findings, waived=())
    except service.NotReviewed:  # the task left reviewed between the load above and the one inside service.approve
        return _not_reviewed_wrong_state_page()
    except service.PlanChanged:
        return _wrong_state_page("The plan changed since you opened this page. Open the task again to review the new plan.")
    except (TaskNotFound, InvalidTaskId):  # the task went between the load above and the one inside service.approve
        return _missing_page()
    except _UNREADABLE:
        return _unreadable_page()
    return RedirectResponse(f"{_base(request)}/tasks/{quote(task.id, safe='')}", status_code=303)


@dataclass(frozen=True)
class _TaskAction:
    """A confirmed task action: its URL name, the confirm page's question and button, and the refusals' wording."""
    name: str
    question: str
    button: str
    gerund: str
    allowed: Callable[[Task], bool]
    wrong_state: str
    enqueue: Callable[[WebActions, str], object] | None = None  # queues the job for a task id; merge has its own post


_ABANDON = _TaskAction(
    "abandon", "Abandon this task? It becomes failed and is no longer worked on. This cannot be undone.", "Abandon task",
    "abandoned", can_abandon, "That task cannot be abandoned in its current state.", WebActions.enqueue_abandon)


_RECOVER = _TaskAction(
    "recover", "Recover this task? It is marked failed as interrupted, and an assumed cost (a multiple of the budget "
    "setting) is recorded against the ledger. Do this only if nothing is still working on it.", "Recover task",
    "recovered", can_recover, "That task cannot be recovered in its current state.", WebActions.enqueue_recover)


_ADVANCE = _TaskAction(
    "advance", "Advance this task? It runs until the next decision point and may spend money on several stages "
    "(up to 10 steps).", "Advance task",
    "advanced", can_advance, "That task cannot be advanced in its current state.", WebActions.enqueue_advance)


_REBASE = _TaskAction(
    "rebase", "Rebase this task onto the current main? Its commits are replayed on main's latest commit and the task "
    "returns to agent done, so it must be verified and audited again (which may spend money). Its audit result and "
    "fix rounds are cleared. If the rebase conflicts, nothing is changed.", "Rebase task",
    "rebased", can_rebase, "That task cannot be rebased in its current state.", WebActions.enqueue_rebase)


_MERGE = _TaskAction(
    "merge", "Merge this task into main? Main is fast-forwarded to the audited commit below and the task's worktree "
    "is removed. This cannot be undone.", "Merge task",
    "merged", can_merge, "That task cannot be merged in its current state.")


def _action_refusal(request: Request, task, action: _TaskAction) -> HTMLResponse | None:
    """The 409 page refusing the action for this task (wrong state, or a job already queued or running), or None."""
    if not action.allowed(task):
        return _wrong_state_page(action.wrong_state)
    if _repo(request).jobs.unfinished_for_task(task.id):
        return _wrong_state_page(f"That task already has a job queued or running, so it cannot be {action.gerund} yet. "
                                 "Open the task and wait for the job to finish.")
    return None


def _action_page(action: _TaskAction):
    async def page(request: Request) -> HTMLResponse:
        task, refusal = _load_task(request)
        if refusal:
            return refusal
        if refusal := _action_refusal(request, task, action):
            return refusal
        detail = {"id": task.id, "goal": _safe(task.goal if task.goal is not None else task.prompt)}
        return HTMLResponse(_TEMPLATES.get_template("action_confirm.html").render(task=detail, action=action, base=_base(request)))
    return page


def _action_post(action: _TaskAction):
    async def post(request: Request) -> RedirectResponse | HTMLResponse:
        task, refusal = _load_task(request)
        if refusal:
            return refusal
        if refusal := _action_refusal(request, task, action):
            return refusal
        try:
            await _read_form(request)  # the confirm form carries no fields; this refuses what the UI's pages would not send
        except _BadForm:
            return _bad_form_page()
        task, refusal = _load_task(request)  # the body read awaited: load and check again, with no await before the submit
        if refusal:
            return refusal
        if refusal := _action_refusal(request, task, action):
            return refusal
        try:
            action.enqueue(_repo(request).actions, task.id)
        except (TaskNotFound, InvalidTaskId):  # the task went between the load above and the one inside the enqueue
            return _missing_page()
        except _UNREADABLE:
            return _unreadable_page()
        return RedirectResponse(f"{_base(request)}/tasks/{quote(task.id, safe='')}", status_code=303)
    return post


async def _merge_page(request: Request) -> HTMLResponse:
    task, refusal = _load_task(request)
    if refusal:
        return refusal
    if refusal := _action_refusal(request, task, _MERGE):
        return refusal
    try:
        preview = _repo(request).actions.merge_preview(task.id)  # git runs on the event loop, as the JSON preview's does
    except MergeRefused as error:
        return _wrong_state_page(f"That task cannot be merged: {_safe(_truncated(str(error), _ERROR_LIMIT))}")
    except InvalidTransition:  # the task left audited between the load above and the one inside the preview
        return _wrong_state_page(_MERGE.wrong_state)
    except (subprocess.CalledProcessError, OSError):  # the text is a git command line or a path, not a reason; a missing worktree is an OSError
        return _wrong_state_page(MERGE_CHECK_FAILED)
    except (TaskNotFound, InvalidTaskId):  # the task went between the load above and the one inside the preview
        return _missing_page()
    except _UNREADABLE:
        return _unreadable_page()
    shown = preview.task
    if not can_merge(shown):  # the preview loaded the task again; it checks the state but not a fix agent in flight
        return _wrong_state_page(_MERGE.wrong_state)
    detail = {"id": shown.id, "goal": _safe(shown.goal if shown.goal is not None else shown.prompt)}
    merge = {"branch": _safe(preview.branch), "audited_sha": _safe(shown.audited_sha), "diffstat": _safe(preview.diffstat),
             "findings": [_safe(finding) for finding in preview.findings],
             "tool_config": [_safe(path) for path in preview.tool_config]}
    return HTMLResponse(_TEMPLATES.get_template("merge_confirm.html").render(task=detail, action=_MERGE, merge=merge, base=_base(request)))


def _shown_sha(form: dict[str, list[str]]) -> str:
    value = _single(form, "audited_sha")
    if value is None or not value.strip():
        raise _BadForm
    return value


async def _merge(request: Request) -> RedirectResponse | HTMLResponse:
    task, refusal = _load_task(request)
    if refusal:
        return refusal
    if refusal := _action_refusal(request, task, _MERGE):  # before the body, so a wrong-state task posted a bad form gets the 409
        return refusal
    try:
        shown_sha = _shown_sha(await _read_form(request))
    except _BadForm:
        return _bad_form_page()
    # The body read awaited: load the task again and check it, with no await from here to the queueing (D8).
    task, refusal = _load_task(request)
    if refusal:
        return refusal
    if refusal := _action_refusal(request, task, _MERGE):
        return refusal
    try:
        _repo(request).actions.enqueue_merge(task.id, shown_sha)  # compares with the audited sha it loads, then queues
    except ShownCommitChanged:
        return _wrong_state_page("The audited commit changed since you opened this page. Open the task again to see "
                                 "what would be merged now.")
    except (TaskNotFound, InvalidTaskId):  # the task went between the load above and the one inside the enqueue
        return _missing_page()
    except _UNREADABLE:
        return _unreadable_page()
    return RedirectResponse(f"{_base(request)}/tasks/{quote(task.id, safe='')}", status_code=303)


_RETRY_WRONG_STATE = "That task cannot be retried in its current state."


def _load_retryable(request: Request):
    """The task if it can be retried, or the page refusing it. No unfinished-job check: retry makes a new task and
    accepts duplicates (D8)."""
    task, refusal = _load_task(request)
    if refusal is None and not can_retry(task):
        return None, _wrong_state_page(_RETRY_WRONG_STATE)
    return task, refusal


async def _retry_page(request: Request) -> HTMLResponse:
    task, refusal = _load_retryable(request)
    if refusal:
        return refusal
    try:
        kept = _kept_plan(_repo(request).actions.keepable_plan(task.id))
    except RetryRefused:
        kept = None
    except (TaskNotFound, InvalidTaskId):  # the task went between the load above and the one inside keepable_plan
        return _missing_page()
    except _UNREADABLE:
        return _unreadable_page()
    detail = {"id": task.id, "goal": _safe(task.goal if task.goal is not None else task.prompt)}
    return HTMLResponse(_TEMPLATES.get_template("retry_confirm.html").render(task=detail, kept=kept, base=_base(request)))


async def _retry(request: Request) -> RedirectResponse | HTMLResponse:
    _, refusal = _load_retryable(request)  # before the body, so a wrong-state task posted a bad form gets the 409
    if refusal:
        return refusal
    try:
        shown_digest = _retry_choice(await _read_form(request))
    except _BadForm:
        return _bad_form_page()
    # The body read awaited: load the task again so a task that stopped being retryable gets the wrong-state page.
    task, refusal = _load_retryable(request)
    if refusal:
        return refusal
    try:
        new_id = _repo(request).actions.retry(task.id, shown_digest)
    except RetryRefused:  # its text names the task and the domain's reason; neither is shown
        if shown_digest is None:
            return _wrong_state_page(_RETRY_WRONG_STATE)
        return _wrong_state_page("The plan changed since you opened this page. Open the task again to see what "
                                 "a retry would keep now.")
    except InvalidWaiver:  # the UI sends no waivers; a ValueError, so it must come before _UNREADABLE
        return _wrong_state_page("That task cannot be retried with this form.")
    except (TaskNotFound, InvalidTaskId):  # the task went between the load above and the one inside the retry
        return _missing_page()
    except _UNREADABLE:
        return _unreadable_page()
    return RedirectResponse(f"{_base(request)}/tasks/{quote(new_id, safe='')}", status_code=303)


def _retry_choice(form: dict[str, list[str]]) -> str | None:
    """The plan digest shown on the page for a keep-plan retry, or None for a retry from scratch."""
    if _single(form, "keep_plan") is None:
        return None
    return _shown_digest(form)


def _kept_plan(task) -> dict:
    """What a keep-plan retry carries over, from the one load that passed the keepable check, with that plan's digest."""
    return {"plan": _safe(task.plan), "plan_digest": plan_digest(task.plan),
            "audit_findings": [_safe(finding) for finding in gaps_to_carry(task)]}


def _new_task_page_with(message: str | None, goal: str, grill: bool, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(_TEMPLATES.get_template("new_task.html").render(message=message, goal=goal, grill=grill),
                        status_code=status_code)


async def _new_task_page(request: Request) -> HTMLResponse:
    return _new_task_page_with(None, "", request.app.state.settings.grill)


def _as_sentence(text: str) -> str:
    """The shared InvalidInput text (also sent in JSON) as a capitalised sentence with a full stop."""
    sentence = text[:1].upper() + text[1:]
    return sentence if sentence.endswith(".") else sentence + "."


async def _new_task(request: Request) -> RedirectResponse | HTMLResponse:
    try:
        form = await _read_form(request)
        goal = _single(form, "goal")
        if goal is None:
            raise _BadForm
        grill = _single(form, "grill") is not None
    except _BadForm:
        return _bad_form_page()
    try:
        if not goal.strip():
            raise InvalidInput("The goal cannot be blank.")
        task_id = request.app.state.actions.create(goal, grill)
    except InvalidInput as error:
        shown_goal = goal.encode(errors="replace").decode()  # a lone surrogate cannot be written to the response
        return _new_task_page_with(_as_sentence(str(error)), shown_goal, grill, status_code=422)
    return RedirectResponse(f"/ui/tasks/{quote(task_id, safe='')}", status_code=303)


def _render_task(task, job=None, base="/ui") -> str:
    return _TEMPLATES.get_template("_task.html").render(
        task=_task_detail(task), job=_job_view(job), actions=_offered_actions(task, job), base=base)


def render_tasks(store) -> str:
    return _TEMPLATES.get_template("_tasks.html").render(_tasks_context(store))


async def _ui_page(request: Request) -> HTMLResponse:
    return HTMLResponse(_TEMPLATES.get_template("page.html").render(_tasks_context(request.app.state.store)))


def _repo_section(registry, name: str) -> dict:
    section = {"name": name, "base": f"/ui/repos/{name}"}
    try:
        return {**section, **_tasks_context(registry.get(name).store)}
    except (sqlite3.Error, OSError):  # one repo's broken database must not take the index down
        return {**section, "unreadable": True}


def _repos_context(registry) -> dict:
    return {"repos": [_repo_section(registry, name) for name in registry.started_names]}


async def _repos_index(request: Request) -> HTMLResponse:
    return HTMLResponse(_TEMPLATES.get_template("repos_page.html").render(_repos_context(request.app.state.registry)))


def _task_routes(prefix: str) -> list[Route]:
    """The pages of one task, under `prefix` (`/ui`, or `/ui/repos/{repo}`)."""
    task = f"{prefix}/tasks/{{task_id}}"
    pages = (("reject", _reject_page, _reject), ("answer", _answer_page, _answer), ("approve", _approve_page, _approve),
             ("abandon", _action_page(_ABANDON), _action_post(_ABANDON)),
             ("recover", _action_page(_RECOVER), _action_post(_RECOVER)),
             ("advance", _action_page(_ADVANCE), _action_post(_ADVANCE)),
             ("rebase", _action_page(_REBASE), _action_post(_REBASE)),
             ("merge", _merge_page, _merge), ("retry", _retry_page, _retry))
    routes = [Route(task, _task_page, methods=["GET"])]
    for name, page, post in pages:
        routes += [Route(f"{task}/{name}", page, methods=["GET"]), Route(f"{task}/{name}", post, methods=["POST"])]
    return [*routes, Route(f"{task}/events", _task_events, methods=["GET"])]


def repos_ui_routes() -> list[Route | Mount]:
    """The UI of the multi-repo server."""
    return [Route("/ui", _ui_without_slash, methods=["GET"]), Route("/ui/", _repos_index, methods=["GET"]),
            *_task_routes("/ui/repos/{repo}")]


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
    return _stream(request, _unless_unchanged(lambda: _event("tasks", render_tasks(store))))


async def _task_events(request: Request) -> StreamingResponse | HTMLResponse:
    _, refusal = _load_task(request)  # before the stream exists, so a refusal still has its real status
    if refusal:
        return refusal
    repo, task_id, base = _repo(request), request.path_params["task_id"], _base(request)
    return _stream(request, _unless_unchanged(lambda: _event("task", _task_fragment(repo.store, repo.jobs, task_id, base))))


def _task_fragment(store, jobs, task_id: str, base: str = "/ui") -> str:
    """The task fragment with its latest job, or the unreadable notice when a later write left the row unreadable;
    never the raw row."""
    try:
        return _render_task(store.load(task_id), jobs.latest_for_task(task_id), base)
    except _UNREADABLE:
        return _TEMPLATES.get_template("_task_unreadable.html").render()


def _unless_unchanged(render):
    """`render`, but None when it gives what it gave last time: any task or job store write wakes the stream, and resending
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
        return HTMLResponse("<!doctype html><title>Factory</title><p>That login link is used or expired. Run <code>factory ui</code>.</p>",
                            status_code=401)
    response = HTMLResponse('<!doctype html><title>Factory</title><p><a href="/ui/">Open the Factory UI</a></p>')
    response.set_cookie(session_cookie_name(request.headers.get("host", "")), state.sessions.create(),
                        httponly=True, samesite="strict", path="/ui")
    return response


STATIC_DIR = Path(__file__).parent / "static"


def ui_routes() -> list[Route | Mount]:
    return [Route("/ui", _ui_without_slash, methods=["GET"]), Route("/ui/", _ui_page, methods=["GET"]),
            Route("/ui/events", _events, methods=["GET"]), Route("/ui/costs", _costs_page, methods=["GET"]),
            Route("/ui/jobs", _jobs_page, methods=["GET"]),
            Route("/ui/clean", _clean_page, methods=["GET"]), Route("/ui/clean", _clean, methods=["POST"]),
            Route("/ui/tasks/new", _new_task_page, methods=["GET"]),
            Route("/ui/tasks/new", _new_task, methods=["POST"]),
            *_task_routes("/ui"),
            Route("/ui/login", _login, methods=["GET"]), Mount("/ui/static", StaticFiles(directory=STATIC_DIR))]
