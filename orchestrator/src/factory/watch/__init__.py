import sys
from pathlib import Path

from factory.client import ServerRefused, ServerUnreachable
from factory.comments import strip_home
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
from factory.watch.planning import (
    _approve_on_thumbs_up,
    _forward_answers,
    _post_plan,
    _post_questions,
)
from factory.watch.posting import _post_final
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
































































































_APPROVED_STATES = {"queued", "running", "agent_done", "verified"}  # the task was approved, here or at the terminal


def run_pass(github, server, state_dir, approver_ids: set[int], clock=_utc_now, home: Path | None = None, merge: bool = False, retry: bool = False) -> None:
    """One pass over the repo's issues: start a task for each new approver issue, post its plan once reviewed, approve it on an approver's 👍."""
    state = load_state(state_dir, clock)
    home = str(home or Path.home())
    try:
        self_id = github.self_user().id
        issues = parse_issues(github.open_issues(), approver_ids, state.watching_since)
    except GitHubError as error:
        _log_failure("the pass", error.stderr)
        return
    for issue in issues:
        try:
            _handle_issue(github, server, state, state_dir, issue, approver_ids, self_id, home, merge, retry)
        except GitHubError as error:
            _log_failure(f"issue #{issue.number}", error.stderr)
        except (ServerUnreachable, ServerRefused) as error:
            _log_failure(f"issue #{issue.number}", str(error))


def _failure_text(failed: dict, home: str) -> str:
    return strip_home(failed["failure_reason"] or "No reason recorded.", home)


def _log_failure(subject: str, text: str) -> None:
    """One escaped line on stderr: gh and server text is not trusted, and a newline would forge a second log line."""
    print(f"watch: {subject} left for the next pass: {escape_control(text.strip()).replace(chr(10), '\\n')}", file=sys.stderr)


def _handle_issue(github, server, state, state_dir, issue: Issue, approver_ids: set[int], self_id: int, home: str, merge: bool, retry: bool) -> None:
    thread = IssueThread(github, issue.number, self_id)
    if issue.number not in state.issues:
        task_id = server.post("/tasks", {"goal": _goal(issue)})["id"]
        state.issues[issue.number] = IssueRecord(task_id)
        save_state(state_dir, state)
    record = state.issues[issue.number]
    if record.phase not in (Phase.NEW, Phase.AWAITING_ANSWERS, Phase.AWAITING_APPROVAL, Phase.APPROVED, Phase.AWAITING_MERGE, Phase.AWAITING_REBASE, Phase.AWAITING_RETRY):
        return
    task = server.get(f"/tasks/{require_safe_id(record.task_id)}")
    if retry and record.phase is Phase.AWAITING_RETRY:
        _retry_on_thumbs_up(github, server, state, state_dir, issue.number, record, approver_ids, self_id, home)
        return
    if task["state"] == "failed":
        _post_failure(github, state, state_dir, issue.number, record, self_id, _failure_text(task, home), retry)
        return
    if record.phase is Phase.AWAITING_MERGE and task["state"] == "audited":
        _merge_on_thumbs_up(thread, server, state, state_dir, record, approver_ids)
        return
    if record.phase in (Phase.AWAITING_MERGE, Phase.AWAITING_REBASE) and task["state"] == "merged":  # merged at the terminal, or the 👍 job's post was lost
        _post_merged(thread, state, state_dir, record)
        return
    if record.phase is Phase.AWAITING_REBASE and task["state"] == "audited":
        _rebase_on_thumbs_up(thread, server, state, state_dir, record, task, approver_ids, home)
        return
    if merge and record.phase is Phase.APPROVED and task["state"] == "audited":  # audited before its comments were posted
        _post_audited(thread, server, state, state_dir, record, {"steps": [], "state": "audited"}, merge)
        return
    if task["state"] in ("audited", "merged"):  # finished at the terminal; no steps to report
        record.phase = Phase.AUDITED
        save_state(state_dir, state)
        return
    if task["state"] in _APPROVED_STATES:
        record.phase = Phase.APPROVED
        save_state(state_dir, state)
    if record.phase is Phase.AWAITING_ANSWERS:
        if task["state"] == "open_questions" and task["grill_rounds"] == record.questions_round:
            if not _forward_answers(thread, server, state, state_dir, record, approver_ids):
                return
        else:
            record.phase = Phase.NEW
            save_state(state_dir, state)
    if record.phase is Phase.AWAITING_APPROVAL:
        _approve_on_thumbs_up(thread, server, state, state_dir, record, approver_ids)
        return
    result = _advance(server, record.task_id)
    if result is None:
        return
    if result["stopped"]:
        _post_final(github, state, state_dir, issue.number, record, self_id, Phase.STOPPED, "Stopped", strip_home(result["stopped"], home))
    elif result["state"] == "open_questions" and record.phase is Phase.NEW:
        _post_questions(thread, server, state, state_dir, record)
    elif result["state"] == "reviewed" and record.phase is Phase.NEW:
        _post_plan(thread, server, state, state_dir, record)
    elif result["state"] == "audited" and record.phase is Phase.APPROVED:
        _post_audited(thread, server, state, state_dir, record, result, merge)
    elif result["state"] == "failed":
        _post_failure(github, state, state_dir, issue.number, record, self_id, _failure_text(result, home), retry)
