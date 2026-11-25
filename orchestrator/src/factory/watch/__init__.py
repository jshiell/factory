import sys
from contextlib import suppress
from pathlib import Path

from factory.client import ServerRefused, ServerUnreachable
from factory.comments import render_comment, strip_home
from factory.failure_codes import explain
from factory.github import GitHubError
from factory.ids import require_safe_id
from factory.textsafe import escape_control
from factory.watch.issues import Issue, _goal, parse_issues
from factory.watch.merging import (
    _merge_on_thumbs_up,
    _post_audited,
    _post_merged,
    _rebase_on_thumbs_up,
)
from factory.watch.options import Options
from factory.watch.planning import (
    _approve_on_thumbs_up,
    _forward_answers,
    _post_plan,
    _post_questions,
)
from factory.watch.posting import _post_final, _post_result
from factory.watch.progress import Progress
from factory.watch.retrying import _post_failure, _retry_on_thumbs_up
from factory.watch.state import IssueRecord, Phase, _utc_now, load_state, save_state
from factory.watch.state import WatchState as WatchState
from factory.watch.state import WatchStateError as WatchStateError
from factory.watch.thread import IssueThread


def _advance(server, task_id: str) -> dict | None:
    """The advance result; None when the job ended failed or interrupted (retry next pass)."""
    job_id = server.post(f"/tasks/{require_safe_id(task_id)}/advance")["job"]["id"]
    job = server.wait(job_id)
    return job["result"] if job["state"] == "succeeded" else None
































































































GIVE_UP_AFTER = 20  # consecutive failed passes; about 20 minutes at the default 60 second interval

_APPROVED_STATES = {"queued", "running", "agent_done", "verified"}  # the task was approved, here or at the terminal


def run_pass(github, server, state_dir, approver_ids: set[int], clock=_utc_now, home: Path | None = None, merge: bool = False, retry: bool = False) -> None:
    """One pass over the repo's issues: start a task for each new approver issue, post its plan once reviewed, approve it on an approver's 👍."""
    state = load_state(state_dir, clock)
    home = str(home or Path.home())
    options = Options(approver_ids, home)
    try:
        self_id = github.self_user().id
        issues = parse_issues(github.open_issues(), approver_ids, state.watching_since)
    except GitHubError as error:
        _log_failure("the pass", error.stderr)
        return
    for issue in issues:
        try:
            _handle_issue(github, server, state, state_dir, issue, options, self_id, merge, retry)
        except GitHubError as error:
            _log_failure(f"issue #{issue.number}", error.stderr)
            _count_failed_pass(github, state, state_dir, issue.number, self_id, options.home, error.stderr)
        except ServerRefused as error:
            _log_failure(f"issue #{issue.number}", str(error))
            _count_failed_pass(github, state, state_dir, issue.number, self_id, options.home, str(error))
        except ServerUnreachable as error:
            _log_failure(f"issue #{issue.number}", str(error))
        else:
            _reset_failed_passes(state, state_dir, issue.number)


def _count_failed_pass(github, state, state_dir, number: int, self_id: int, home: str, last_error: str) -> None:
    if number not in state.issues:
        return
    state.failed_passes[number] = state.failed_passes.get(number, 0) + 1
    save_state(state_dir, state)
    if state.failed_passes[number] >= GIVE_UP_AFTER:
        _give_up(github, state, state_dir, number, self_id, home, last_error)


def _give_up(github, state, state_dir, number: int, self_id: int, home: str, last_error: str) -> None:
    """Stop the issue for good and say so once; the stopped phase is skipped by every later pass.
    Nothing escapes: if the comment cannot be posted the issue is stopped silently and the log is the trace."""
    try:
        _post_gave_up(github, state, state_dir, number, self_id, home, last_error)
    except Exception as error:
        _log_line(f"issue #{number} stopped without a comment", str(error))
        state.issues[number].phase = Phase.STOPPED
        with suppress(Exception):  # unsaved, the next pass counts it past the cap again and stops it again
            save_state(state_dir, state)


def _post_gave_up(github, state, state_dir, number: int, self_id: int, home: str, last_error: str) -> None:
    record = state.issues[number]
    sections = [
        ("Stopped", f"The watcher stopped retrying this issue after {GIVE_UP_AFTER} failed passes and no longer acts on reactions to its earlier comments here.", False),
        ("Last error", escape_control(strip_home(last_error, home).strip()), True),
        ("Next", f"Inspect the task at the terminal: factory show {require_safe_id(record.task_id)}", False),
    ]
    body = render_comment(record.task_id, "gave-up", sections)
    _post_result(IssueThread(github, number, self_id), Progress(state, state_dir, record), "gave-up", Phase.STOPPED, body)


def _reset_failed_passes(state, state_dir, number: int) -> None:
    if state.failed_passes.pop(number, 0):
        save_state(state_dir, state)


def _failure_text(failed: dict, home: str) -> str:
    return strip_home(failed["failure_reason"] or "No reason recorded.", home)


def _failure_explanation(failed: dict) -> str | None:
    explanation = explain("failure_reason", failed["failure_reason"]) if failed["failure_reason"] else None
    return explanation.text if explanation else None


def _stop_explanation(result: dict) -> str | None:
    error = result.get("stopped_error")
    explanation = explain(error["field"], error["code"]) if error else None
    return explanation.text if explanation else None


def _log_failure(subject: str, text: str) -> None:
    """One escaped line on stderr: gh and server text is not trusted, and a newline would forge a second log line."""
    _log_line(f"{subject} left for the next pass", text)


def _log_line(what: str, text: str) -> None:
    print(f"watch: {what}: {escape_control(text.strip()).replace(chr(10), '\\n')}", file=sys.stderr)


def _handle_issue(github, server, state, state_dir, issue: Issue, options: Options, self_id: int, merge: bool, retry: bool) -> None:
    thread = IssueThread(github, issue.number, self_id)
    if issue.number not in state.issues:
        task_id = server.post("/tasks", {"goal": _goal(issue)})["id"]
        state.issues[issue.number] = IssueRecord(task_id)
        state.failed_passes.pop(issue.number, None)
        save_state(state_dir, state)
    record = state.issues[issue.number]
    progress = Progress(state, state_dir, record)
    if record.phase not in (Phase.NEW, Phase.AWAITING_ANSWERS, Phase.AWAITING_APPROVAL, Phase.APPROVED, Phase.AWAITING_MERGE, Phase.AWAITING_REBASE, Phase.AWAITING_RETRY):
        return
    task = server.get(f"/tasks/{require_safe_id(record.task_id)}")
    if retry and record.phase is Phase.AWAITING_RETRY:
        _retry_on_thumbs_up(thread, server, state, progress, options)
        return
    if task["state"] == "failed":
        _post_failure(thread, progress, _failure_text(task, options.home), retry, _failure_explanation(task))
        return
    if record.phase is Phase.AWAITING_MERGE and task["state"] == "audited":
        _merge_on_thumbs_up(thread, server, progress, options)
        return
    if record.phase in (Phase.AWAITING_MERGE, Phase.AWAITING_REBASE) and task["state"] == "merged":  # merged at the terminal, or the 👍 job's post was lost
        _post_merged(thread, progress)
        return
    if record.phase is Phase.AWAITING_REBASE and task["state"] == "audited":
        _rebase_on_thumbs_up(thread, server, progress, task, options)
        return
    if merge and record.phase is Phase.APPROVED and task["state"] == "audited":  # audited before its comments were posted
        _post_audited(thread, server, progress, {"steps": [], "state": "audited"}, merge)
        return
    if task["state"] in ("audited", "merged"):  # finished at the terminal; no steps to report
        record.phase = Phase.AUDITED
        progress.save()
        return
    if task["state"] in _APPROVED_STATES:
        record.phase = Phase.APPROVED
        progress.save()
    if record.phase is Phase.AWAITING_ANSWERS:
        if task["state"] == "open_questions" and task["grill_rounds"] == record.questions_round:
            if not _forward_answers(thread, server, progress, options):
                return
        else:
            record.phase = Phase.NEW
            progress.save()
    if record.phase is Phase.AWAITING_APPROVAL:
        _approve_on_thumbs_up(thread, server, progress, options)
        return
    result = _advance(server, record.task_id)
    if result is None:
        return
    if result["stopped"]:
        _post_final(thread, progress, Phase.STOPPED, "Stopped", strip_home(result["stopped"], options.home), _stop_explanation(result))
    elif result["state"] == "open_questions" and record.phase is Phase.NEW:
        _post_questions(thread, server, progress)
    elif result["state"] == "reviewed" and record.phase is Phase.NEW:
        _post_plan(thread, server, progress)
    elif result["state"] == "audited" and record.phase is Phase.APPROVED:
        _post_audited(thread, server, progress, result, merge)
    elif result["state"] == "failed":
        _post_failure(thread, progress, _failure_text(result, options.home), retry, _failure_explanation(result))
