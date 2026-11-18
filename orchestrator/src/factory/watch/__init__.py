import hashlib
import sys
from pathlib import Path

from factory.answer_questions import MAX_ANSWER_BYTES
from factory.client import ServerRefused, ServerUnreachable
from factory.comments import has_marker, render_comment, strip_home
from factory.github import GitHubError
from factory.ids import require_safe_id
from factory.merge_task import ALREADY_IN_MAIN, MAIN_MOVED
from factory.question_text import questions_text
from factory.textsafe import escape_control
from factory.watch.issues import Issue, _goal, parse_issues
from factory.watch.state import IssueRecord, Phase, _utc_now, load_state, save_state
from factory.watch.state import WatchState as WatchState
from factory.watch.state import WatchStateError as WatchStateError

_HOW_TO_ACT = "Task {task_id}. React 👍 to approve this plan, 👎 to reject it."


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _advance(server, task_id: str) -> dict | None:
    """The advance result; None when the job ended failed or interrupted (retry next pass)."""
    job_id = server.post(f"/tasks/{require_safe_id(task_id)}/advance")["job"]["id"]
    job = server.wait(job_id)
    return job["result"] if job["state"] == "succeeded" else None


def _findings_text(approval: dict) -> str:
    return "\n".join(f"- {finding}" for finding in approval["review_findings"])


def _post_plan(github, server, state, state_dir, issue_number: int, record: IssueRecord, self_id: int) -> None:
    approval = server.get(f"/tasks/{require_safe_id(record.task_id)}/approval")
    findings = _findings_text(approval)
    required = "\n\n".join(filter(None, ["\n".join(approval["required"]), approval["warning"]]))
    body = render_comment(record.task_id, "plan", [
        ("Task", _HOW_TO_ACT.format(task_id=record.task_id), False),
        ("Plan", approval["plan"], True),
        ("Review findings", findings, True),
        ("Required review notes", required, True),
    ])
    exact_copy = _own_comment_id(github, issue_number, self_id, lambda c: c.body == body)
    record.plan_comment_id = exact_copy or github.comment(issue_number, body)
    record.posted_digest = approval["plan_digest"]
    record.plan_body_hash = _sha256(body)
    record.findings_hash = _sha256(findings)
    record.phase = Phase.AWAITING_APPROVAL
    save_state(state_dir, state)


_HOW_TO_ANSWER = "Task {task_id}. Reply to this comment with your answers; the plan is written once you have answered."


def _post_questions(github, server, state, state_dir, issue_number: int, record: IssueRecord, self_id: int) -> None:
    shown = server.get(f"/tasks/{require_safe_id(record.task_id)}/questions")
    body = render_comment(record.task_id, "questions", [
        ("Task", _HOW_TO_ANSWER.format(task_id=record.task_id), False),
        (f"Questions (round {shown['round']})", questions_text(shown["questions"]), True),
    ])
    exact_copy = _own_comment_id(github, issue_number, self_id, lambda c: c.body == body)
    record.questions_comment_id = exact_copy or github.comment(issue_number, body)
    record.questions_round = shown["round"]
    record.phase = Phase.AWAITING_ANSWERS
    save_state(state_dir, state)


def _is_factory_comment(comment) -> bool:
    return comment.body.split("\n", 1)[0].startswith("<!-- factory:task=")


def _answers_after(comments, questions_comment_id: int, approver_ids: set[int]) -> list[str] | None:
    """Approver comments after the questions comment, by list position (comments carry no timestamp); None when that comment is gone."""
    ids = [c.id for c in comments]
    if questions_comment_id not in ids:
        return None
    later = comments[ids.index(questions_comment_id) + 1:]
    return [c.body for c in later if c.user_id in approver_ids and not _is_factory_comment(c)]


def _within_cap(text: str) -> str:
    """Cut to the server's byte limit, never inside a character."""
    return text.encode()[:MAX_ANSWER_BYTES].decode(errors="ignore")


_QUESTIONS_GONE = (
    "The questions comment was deleted, so answers here can no longer be matched to it. "
    "Answer at the terminal."
)


def _forward_answers(github, server, state, state_dir, issue_number: int, record: IssueRecord, approver_ids: set[int], self_id: int) -> bool:
    """Send the approvers' answers to the server; True when the wait is over."""
    answers = _answers_after(github.comments(issue_number), record.questions_comment_id, approver_ids)
    if answers is None:
        text = f"{_QUESTIONS_GONE} Run: factory answer {require_safe_id(record.task_id)}"
        _post_final(github, state, state_dir, issue_number, record, self_id, Phase.STOPPED, "Stopped", text)
        return False
    if not answers:
        return False
    try:
        server.post(
            f"/tasks/{require_safe_id(record.task_id)}/answer",
            {"round": record.questions_round, "answers": _within_cap("\n\n".join(answers))},
        )
    except ServerRefused as refusal:  # the task moved on at the terminal; the next step reads where it is now
        if (refusal.status, refusal.error) not in ((409, "round changed"), (409, "wrong state")):
            raise
    record.phase = Phase.NEW
    save_state(state_dir, state)
    return True


def _own_comment_id(github, issue_number: int, self_id: int, matches) -> int | None:
    """A comment this watcher posted before a crash lost its id; authorship is by numeric id, never login, so nobody else's comment is adopted."""
    return next((c.id for c in github.comments(issue_number) if c.user_id == self_id and matches(c)), None)


def _result_comment_id(github, issue_number: int, record: IssueRecord, self_id: int, kind: str, body: str) -> int:
    """Post the task's ending, or adopt this watcher's earlier one; nothing downstream reads its text, so the marker alone identifies it."""
    earlier = _own_comment_id(github, issue_number, self_id, lambda c: has_marker(c.body, record.task_id, kind))
    return earlier or github.comment(issue_number, body)


def _post_result(github, state, state_dir, issue_number: int, record: IssueRecord, self_id: int, kind: str, phase: Phase, body: str) -> None:
    record.result_comment_id = _result_comment_id(github, issue_number, record, self_id, kind, body)
    record.phase = phase
    save_state(state_dir, state)


def _post_final(github, state, state_dir, issue_number: int, record: IssueRecord, self_id: int, phase: Phase, heading: str, text: str) -> None:
    body = render_comment(record.task_id, "result", [(heading, text, True)])
    _post_result(github, state, state_dir, issue_number, record, self_id, "result", phase, body)


_HOW_TO_RETRY = (
    "Task {task_id}. React 👍 to retry: a new task from the original goal, not the issue's current text, "
    "which plans and reviews again (this spends agent budget; the new plan needs its own 👍), 👎 to leave it."
)


def _post_failure(github, state, state_dir, issue_number: int, record: IssueRecord, self_id: int, reason: str, retry: bool) -> None:
    """The failure comment; with retry on, also the retry offer. Both are adopted or posted before one save, so a lost post leaves the issue for the next pass."""
    failure = render_comment(record.task_id, "result", [("Failure reason", reason, True)])
    if not retry:
        _post_result(github, state, state_dir, issue_number, record, self_id, "result", Phase.FAILED, failure)
        return
    offer = render_comment(record.task_id, "retry-offer", [("Retry", _HOW_TO_RETRY.format(task_id=record.task_id), False), ("Failure reason", reason, True)])
    failure_id = _result_comment_id(github, issue_number, record, self_id, "result", failure)
    record.retry_comment_id = _result_comment_id(github, issue_number, record, self_id, "retry-offer", offer)
    record.result_comment_id = failure_id
    record.phase = Phase.AWAITING_RETRY
    save_state(state_dir, state)


_HOW_TO_MERGE = "Task {task_id}. React 👍 to merge, 👎 to leave it unmerged."


def _post_audited(github, server, state, state_dir, issue_number: int, record: IssueRecord, self_id: int, result: dict, merge: bool) -> None:
    steps = "\n".join(f"{step['stage']} -> {step['state']}" for step in result["steps"])
    body = render_comment(record.task_id, "result", [
        ("Steps", steps, True),
        ("State", result["state"], True),
        ("Merge", f"When ready, run: factory merge {require_safe_id(record.task_id)}", False),
    ])
    if not merge:
        _post_result(github, state, state_dir, issue_number, record, self_id, "result", Phase.AUDITED, body)
        return
    record.result_comment_id = _result_comment_id(github, issue_number, record, self_id, "result", body)
    _post_merge_comment(github, server, state, state_dir, issue_number, record, self_id)


def _post_merge_refused(github, state, state_dir, issue_number: int, record: IssueRecord, self_id: int, reason: str) -> None:
    task_id = require_safe_id(record.task_id)
    if reason in (ALREADY_IN_MAIN, _MERGE_JOB_ALREADY_IN_MAIN):
        next_step = "Nothing was merged by the factory: the audited commit is already in main. Check main, then close this issue."
    else:
        next_step = f"Nothing was merged. At the terminal, run: factory rebase {task_id} if main moved, or factory merge {task_id}."
    body = render_comment(record.task_id, "merge-refused", [
        ("Merge refused", reason, True),
        ("Next", next_step, False),
    ])
    _post_result(github, state, state_dir, issue_number, record, self_id, "merge-refused", Phase.MERGE_STOPPED, body)


_HOW_TO_REBASE = (
    "`main` moved since the audit. React 👍 to rebase the task onto `main` and re-audit it "
    "(this spends agent budget), 👎 to leave it."
)


def _post_merge_moved(github, server, state, state_dir, issue_number: int, record: IssueRecord, self_id: int) -> None:
    """Offer the rebase. The body carries the audited commit, so no earlier round's offer (with its 👍) can match it."""
    audited_sha = server.get(f"/tasks/{require_safe_id(record.task_id)}")["audited_sha"]
    body = render_comment(record.task_id, "merge-moved", [
        ("Task", _HOW_TO_REBASE, False),
        ("Merge refused", MAIN_MOVED, True),
        ("Audited commit", audited_sha, True),
    ])
    exact_copy = _own_comment_id(github, issue_number, self_id, lambda c: c.body == body)
    record.rebase_comment_id = exact_copy or github.comment(issue_number, body)
    record.rebase_sha = audited_sha
    record.phase = Phase.AWAITING_REBASE
    save_state(state_dir, state)


def _refuse_merge(github, server, state, state_dir, issue_number: int, record: IssueRecord, self_id: int, reason: str) -> None:
    """Main having moved can be fixed by a rebase; any other refusal is for the terminal."""
    if reason == MAIN_MOVED:
        _post_merge_moved(github, server, state, state_dir, issue_number, record, self_id)
    else:
        _post_merge_refused(github, state, state_dir, issue_number, record, self_id, reason)


def _merge_preview_or_refuse(github, server, state, state_dir, issue_number: int, record: IssueRecord, self_id: int) -> dict | None:
    """The server's merge preview; if the server refuses the merge, post the refusal and return None."""
    try:
        return server.get(f"/tasks/{require_safe_id(record.task_id)}/merge-preview")
    except ServerRefused as refusal:
        if refusal.error != "merge refused":
            raise
        _refuse_merge(github, server, state, state_dir, issue_number, record, self_id, refusal.detail)
        return None


def _post_merge_comment(github, server, state, state_dir, issue_number: int, record: IssueRecord, self_id: int) -> None:
    preview = _merge_preview_or_refuse(github, server, state, state_dir, issue_number, record, self_id)
    if preview is None:
        return
    body = render_comment(record.task_id, "merge", [
        ("Task", _HOW_TO_MERGE.format(task_id=record.task_id), False),
        ("Audited commit", preview["audited_sha"], True),
        ("Branch", preview["branch"], True),
        ("Changes", preview["diffstat"], True),
        ("Audit findings", "\n".join(f"- {finding}" for finding in preview["findings"]), True),
    ])
    exact_copy = _own_comment_id(github, issue_number, self_id, lambda c: c.body == body)
    record.merge_comment_id = exact_copy or github.comment(issue_number, body)
    record.merge_body_hash = _sha256(body)
    record.merge_sha = preview["audited_sha"]
    record.phase = Phase.AWAITING_MERGE
    save_state(state_dir, state)


_CHANGED = (
    "The plan comment, the plan or its review findings changed after the plan comment was posted, "
    "so no approval was made. Review the task at the terminal."
)


_REJECTED = "An approver rejected this plan, so the task is rejected and no work will be done."


def _post_ending(github, state, state_dir, issue_number: int, record: IssueRecord, self_id: int, kind: str, phase: Phase, heading: str, text: str) -> None:
    body = render_comment(record.task_id, kind, [(heading, text, False)])
    _post_result(github, state, state_dir, issue_number, record, self_id, kind, phase, body)


def _post_changed(github, state, state_dir, issue_number: int, record: IssueRecord, self_id: int) -> None:
    _post_ending(github, state, state_dir, issue_number, record, self_id, "changed", Phase.CHANGED, "Plan changed", _CHANGED)


def _post_rejected(github, state, state_dir, issue_number: int, record: IssueRecord, self_id: int) -> None:
    _post_ending(github, state, state_dir, issue_number, record, self_id, "rejected", Phase.REJECTED, "Plan rejected", _REJECTED)


def _approver_reacted(reactions, content: str, approver_ids: set[int]) -> bool:
    """Approvers match by numeric id, never login."""
    return any(r.content == content and r.user_id in approver_ids for r in reactions)


def _approval_if_as_posted(github, server, issue_number: int, record: IssueRecord) -> dict | None:
    """The server's approval preview, if the plan comment is unedited and the preview's plan digest and findings are the ones it showed."""
    posted = [_sha256(c.body) for c in github.comments(issue_number) if c.id == record.plan_comment_id]
    if posted != [record.plan_body_hash]:
        return None
    approval = server.get(f"/tasks/{require_safe_id(record.task_id)}/approval")
    if (approval["plan_digest"], _sha256(_findings_text(approval))) != (record.posted_digest, record.findings_hash):
        return None
    return approval


def _approve_on_thumbs_up(github, server, state, state_dir, issue_number: int, record: IssueRecord, approver_ids: set[int], self_id: int) -> None:
    """Reject on an approver's 👎 (which wins), else approve on their 👍; approvers match by numeric id, never login. `approved` is saved only after the server accepts."""
    reactions = github.reactions(record.plan_comment_id)
    if _approver_reacted(reactions, "-1", approver_ids):
        server.post(f"/tasks/{require_safe_id(record.task_id)}/reject")
        _post_rejected(github, state, state_dir, issue_number, record, self_id)
        return
    if not _approver_reacted(reactions, "+1", approver_ids):
        return
    approval =_approval_if_as_posted(github, server, issue_number, record)
    if approval is None:
        _post_changed(github, state, state_dir, issue_number, record, self_id)
        return
    try:
        server.post(f"/tasks/{require_safe_id(record.task_id)}/approve", {
            "plan_digest": record.posted_digest, "review_findings": approval["review_findings"], "waive": []})
    except ServerRefused as refusal:
        if (refusal.status, refusal.error) != (409, "plan changed"):
            raise
        _post_changed(github, state, state_dir, issue_number, record, self_id)
        return
    record.phase = Phase.APPROVED
    save_state(state_dir, state)


_MERGED = "Merged. The commits are unsigned: sign them before pushing."


def _post_merged(github, state, state_dir, issue_number: int, record: IssueRecord, self_id: int) -> None:
    _post_ending(github, state, state_dir, issue_number, record, self_id, "merged", Phase.MERGED, "Merged", _MERGED)


_NOT_MERGED = "An approver declined the merge, so nothing was merged. The task stays audited; merge it at the terminal if you change your mind."


_MERGE_CHANGED = (
    "The merge comment, or the audited commit it showed, changed after the comment was posted, "
    "so nothing was merged. Review the task at the terminal."
)


def _post_merge_changed(github, state, state_dir, issue_number: int, record: IssueRecord, self_id: int) -> None:
    _post_ending(github, state, state_dir, issue_number, record, self_id, "merge-changed", Phase.MERGE_STOPPED, "Merge changed", _MERGE_CHANGED)


def _merge_comment_is_unedited(github, issue_number: int, record: IssueRecord) -> bool:
    posted = [_sha256(c.body) for c in github.comments(issue_number) if c.id == record.merge_comment_id]
    return posted == [record.merge_body_hash]


def _task_is_merged(server, task_id: str) -> bool:
    return server.get(f"/tasks/{task_id}")["state"] == "merged"


_MERGE_JOB_MAIN_MOVED = f"merge refused: {MAIN_MOVED}"  # the error a failed merge job carries
_MERGE_JOB_ALREADY_IN_MAIN = f"merge refused: {ALREADY_IN_MAIN}"


def _merge_on_thumbs_up(github, server, state, state_dir, issue_number: int, record: IssueRecord, approver_ids: set[int], self_id: int) -> None:
    """Merge the commit the merge comment showed on an approver's 👍; approvers match by numeric id, never login."""
    reactions = github.reactions(record.merge_comment_id)
    if _approver_reacted(reactions, "-1", approver_ids):
        _post_ending(github, state, state_dir, issue_number, record, self_id, "not-merged", Phase.NOT_MERGED, "Not merged", _NOT_MERGED)
        return
    if not _approver_reacted(reactions, "+1", approver_ids):
        return
    task_id =require_safe_id(record.task_id)
    if not _merge_comment_is_unedited(github, issue_number, record):
        _post_merge_changed(github, state, state_dir, issue_number, record, self_id)
        return
    preview = _merge_preview_or_refuse(github, server, state, state_dir, issue_number, record, self_id)
    if preview is None:
        return
    if preview["audited_sha"] != record.merge_sha:
        _post_merge_changed(github, state, state_dir, issue_number, record, self_id)
        return
    job_id = server.post(f"/tasks/{task_id}/merge", {"audited_sha": record.merge_sha})["job"]["id"]
    job = server.wait(job_id)
    if job["state"] == "succeeded" or _task_is_merged(server, task_id):
        _post_merged(github, state, state_dir, issue_number, record, self_id)
    elif job["state"] == "failed" and job["error"] == _MERGE_JOB_MAIN_MOVED:
        _post_merge_moved(github, server, state, state_dir, issue_number, record, self_id)
    elif job["state"] == "failed":
        _post_merge_refused(github, state, state_dir, issue_number, record, self_id, job["error"])


_NOT_REBASED = "An approver declined the rebase, so nothing was rebased or merged. The task stays audited; rebase it at the terminal if you change your mind."


def _post_rebase_refused(github, state, state_dir, issue_number: int, record: IssueRecord, self_id: int, reason: str) -> None:
    task_id = require_safe_id(record.task_id)
    body = render_comment(record.task_id, "rebase-refused", [
        ("Rebase refused", reason, True),
        ("Next", f"Nothing was rebased. At the terminal, run: factory rebase {task_id} once the cause is fixed.", False),
    ])
    _post_result(github, state, state_dir, issue_number, record, self_id, "rebase-refused", Phase.MERGE_STOPPED, body)


def _task_is_rebased(server, record: IssueRecord) -> bool:
    """A job that failed after the rebase landed (a lost reply, a rebase at the terminal): the task has left the offered commit."""
    task = server.get(f"/tasks/{require_safe_id(record.task_id)}")
    return task["state"] == "agent_done" or task["audited_sha"] != record.rebase_sha


def _rebase_on_thumbs_up(github, server, state, state_dir, issue_number: int, record: IssueRecord, task: dict, approver_ids: set[int], self_id: int, home: str) -> None:
    """Rebase the task on an approver's 👍 on the rebase offer, if it is still at the commit the offer showed; approvers match by numeric id, never login."""
    reactions = github.reactions(record.rebase_comment_id)
    if _approver_reacted(reactions, "-1", approver_ids):
        _post_ending(github, state, state_dir, issue_number, record, self_id, "not-merged", Phase.NOT_MERGED, "Not rebased", _NOT_REBASED)
        return
    if not _approver_reacted(reactions, "+1", approver_ids):
        return
    if task["audited_sha"] == record.rebase_sha:
        job_id = server.post(f"/tasks/{require_safe_id(record.task_id)}/rebase")["job"]["id"]
        job = server.wait(job_id)
        if job["state"] == "failed" and not _task_is_rebased(server, record):
            _post_rebase_refused(github, state, state_dir, issue_number, record, self_id, strip_home(job["error"], home))
            return
        if job["state"] not in ("succeeded", "failed"):
            return
    record.phase = Phase.APPROVED
    save_state(state_dir, state)


_NOT_RETRIED = "An approver declined the retry, so the task stays failed. Retry it at the terminal if you change your mind."


def _post_retry_refused(github, state, state_dir, issue_number: int, record: IssueRecord, self_id: int, reason: str) -> None:
    body = render_comment(record.task_id, "retry-refused", [
        ("Retry refused", reason, True),
        ("Next", f"Nothing was retried. At the terminal, run: factory retry {require_safe_id(record.task_id)} once the cause is fixed.", False),
    ])
    _post_result(github, state, state_dir, issue_number, record, self_id, "retry-refused", Phase.STOPPED, body)


def _retry_on_thumbs_up(github, server, state, state_dir, issue_number: int, record: IssueRecord, approver_ids: set[int], self_id: int, home: str) -> None:
    """Retry the failed task on an approver's 👍 on the offer and follow the new task from the next pass; approvers match by numeric id, never login."""
    reactions = github.reactions(record.retry_comment_id)
    if _approver_reacted(reactions, "-1", approver_ids):
        _post_ending(github, state, state_dir, issue_number, record, self_id, "not-retried", Phase.NOT_MERGED, "Not retried", _NOT_RETRIED)
        return
    if not _approver_reacted(reactions, "+1", approver_ids):
        return
    try:
        new_id = server.post(f"/tasks/{require_safe_id(record.task_id)}/retry")["id"]
    except ServerRefused as refusal:
        if (refusal.status, refusal.error) != (409, "retry refused"):
            raise
        _post_retry_refused(github, state, state_dir, issue_number, record, self_id, strip_home(refusal.detail, home))
        return
    state.issues[issue_number] = IssueRecord(task_id=new_id)
    save_state(state_dir, state)


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
        _merge_on_thumbs_up(github, server, state, state_dir, issue.number, record, approver_ids, self_id)
        return
    if record.phase in (Phase.AWAITING_MERGE, Phase.AWAITING_REBASE) and task["state"] == "merged":  # merged at the terminal, or the 👍 job's post was lost
        _post_merged(github, state, state_dir, issue.number, record, self_id)
        return
    if record.phase is Phase.AWAITING_REBASE and task["state"] == "audited":
        _rebase_on_thumbs_up(github, server, state, state_dir, issue.number, record, task, approver_ids, self_id, home)
        return
    if merge and record.phase is Phase.APPROVED and task["state"] == "audited":  # audited before its comments were posted
        _post_audited(github, server, state, state_dir, issue.number, record, self_id, {"steps": [], "state": "audited"}, merge)
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
            if not _forward_answers(github, server, state, state_dir, issue.number, record, approver_ids, self_id):
                return
        else:
            record.phase = Phase.NEW
            save_state(state_dir, state)
    if record.phase is Phase.AWAITING_APPROVAL:
        _approve_on_thumbs_up(github, server, state, state_dir, issue.number, record, approver_ids, self_id)
        return
    result = _advance(server, record.task_id)
    if result is None:
        return
    if result["stopped"]:
        _post_final(github, state, state_dir, issue.number, record, self_id, Phase.STOPPED, "Stopped", strip_home(result["stopped"], home))
    elif result["state"] == "open_questions" and record.phase is Phase.NEW:
        _post_questions(github, server, state, state_dir, issue.number, record, self_id)
    elif result["state"] == "reviewed" and record.phase is Phase.NEW:
        _post_plan(github, server, state, state_dir, issue.number, record, self_id)
    elif result["state"] == "audited" and record.phase is Phase.APPROVED:
        _post_audited(github, server, state, state_dir, issue.number, record, self_id, result, merge)
    elif result["state"] == "failed":
        _post_failure(github, state, state_dir, issue.number, record, self_id, _failure_text(result, home), retry)
