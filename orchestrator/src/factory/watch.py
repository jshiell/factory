import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from factory.client import ServerRefused
from factory.comments import render_comment, strip_home
from factory.ids import require_safe_id


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    author_id: int
    author_login: str
    created_at: datetime


def parse_issues(raw: list[dict], approver_ids: set[int], watching_since: datetime) -> list[Issue]:
    """The issues to consider: open, not a pull request, by an approver, created since watching began."""
    issues = []
    for r in raw:
        created_at = datetime.fromisoformat(r["created_at"])
        if "pull_request" in r or r["state"] != "open":
            continue
        if r["user"]["id"] not in approver_ids or created_at < watching_since:
            continue
        issues.append(
            Issue(
                number=r["number"],
                title=r["title"],
                body=r["body"] or "",
                author_id=r["user"]["id"],
                author_login=r["user"]["login"],
                created_at=created_at,
            )
        )
    return issues


class Phase(str, Enum):
    NEW = "new"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    AUDITED = "audited"
    FAILED = "failed"
    STOPPED = "stopped"
    REJECTED = "rejected"
    CHANGED = "changed"


@dataclass
class IssueRecord:
    task_id: str
    phase: Phase = Phase.NEW
    plan_comment_id: int | None = None
    posted_digest: str | None = None
    plan_body_hash: str | None = None
    findings_hash: str | None = None
    result_comment_id: int | None = None


@dataclass
class WatchState:
    watching_since: datetime
    cursor: datetime | None = None
    issues: dict[int, IssueRecord] = field(default_factory=dict)


def _state_path(state_dir) -> Path:
    return Path(state_dir) / "watch.json"


def save_state(state_dir, state: WatchState) -> None:
    """Write atomically (temp file in the same directory, then replace) so a crash never leaves a truncated file."""
    path = _state_path(state_dir)
    document = {
        "watching_since": state.watching_since.isoformat(),
        "cursor": state.cursor.isoformat() if state.cursor else None,
        "issues": {str(number): {**vars(record), "phase": record.phase.value} for number, record in state.issues.items()},
    }
    fd, temp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as temp_file:
            json.dump(document, temp_file)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


class WatchStateError(Exception):
    """The state file cannot be read; resetting it silently would re-create tasks for old issues."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_state(state_dir, clock=_utc_now) -> WatchState:
    """The saved state; on the first run, a fresh one whose `watching_since` is `clock()`, saved at once."""
    path = _state_path(state_dir)
    if not path.exists():
        state = WatchState(watching_since=clock())
        save_state(state_dir, state)
        return state
    try:
        document = json.loads(path.read_text())
        return WatchState(
            watching_since=datetime.fromisoformat(document["watching_since"]),
            cursor=datetime.fromisoformat(document["cursor"]) if document["cursor"] else None,
            issues={
                int(number): IssueRecord(**{**record, "phase": Phase(record["phase"])})
                for number, record in document["issues"].items()
            },
        )
    except ValueError as error:
        raise WatchStateError(f"{path} is unreadable ({error}); fix or delete it, but deleting re-creates tasks for old issues") from error


GOAL_LIMIT = 20_000
_GOAL_TRUNCATED = "\n[issue text truncated]"


def _goal(issue: Issue) -> str:
    goal = f"{issue.title}\n\n{issue.body}"
    if len(goal) <= GOAL_LIMIT:
        return goal
    return goal[: GOAL_LIMIT - len(_GOAL_TRUNCATED)] + _GOAL_TRUNCATED


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


def _post_plan(github, server, state, state_dir, issue_number: int, record: IssueRecord) -> None:
    approval = server.get(f"/tasks/{require_safe_id(record.task_id)}/approval")
    findings = _findings_text(approval)
    required = "\n\n".join(filter(None, ["\n".join(approval["required"]), approval["warning"]]))
    body = render_comment(record.task_id, "plan", [
        ("Task", _HOW_TO_ACT.format(task_id=record.task_id), False),
        ("Plan", approval["plan"], True),
        ("Review findings", findings, True),
        ("Required review notes", required, True),
    ])
    record.plan_comment_id = github.comment(issue_number, body)
    record.posted_digest = approval["plan_digest"]
    record.plan_body_hash = _sha256(body)
    record.findings_hash = _sha256(findings)
    record.phase = Phase.AWAITING_APPROVAL
    save_state(state_dir, state)


def _post_final(github, state, state_dir, issue_number: int, record: IssueRecord, phase: Phase, heading: str, text: str) -> None:
    body = render_comment(record.task_id, "result", [(heading, text, True)])
    record.result_comment_id = github.comment(issue_number, body)
    record.phase = phase
    save_state(state_dir, state)


def _post_audited(github, state, state_dir, issue_number: int, record: IssueRecord, result: dict) -> None:
    steps = "\n".join(f"{step['stage']} -> {step['state']}" for step in result["steps"])
    body = render_comment(record.task_id, "result", [
        ("Steps", steps, True),
        ("State", result["state"], True),
        ("Merge", f"When ready, run: factory merge {require_safe_id(record.task_id)}", False),
    ])
    record.result_comment_id = github.comment(issue_number, body)
    record.phase = Phase.AUDITED
    save_state(state_dir, state)


_CHANGED = (
    "The plan comment, the plan or its review findings changed after the plan comment was posted, "
    "so no approval was made. Review the task at the terminal."
)


_REJECTED = "An approver rejected this plan, so the task is rejected and no work will be done."


def _post_ending(github, state, state_dir, issue_number: int, record: IssueRecord, kind: str, phase: Phase, heading: str, text: str) -> None:
    body = render_comment(record.task_id, kind, [(heading, text, False)])
    record.result_comment_id = github.comment(issue_number, body)
    record.phase = phase
    save_state(state_dir, state)


def _post_changed(github, state, state_dir, issue_number: int, record: IssueRecord) -> None:
    _post_ending(github, state, state_dir, issue_number, record, "changed", Phase.CHANGED, "Plan changed", _CHANGED)


def _post_rejected(github, state, state_dir, issue_number: int, record: IssueRecord) -> None:
    _post_ending(github, state, state_dir, issue_number, record, "rejected", Phase.REJECTED, "Plan rejected", _REJECTED)


def _still_as_posted(github, server, issue_number: int, record: IssueRecord) -> bool:
    """The plan comment is unedited and the server's plan digest and findings are the ones it showed."""
    posted = [_sha256(c.body) for c in github.comments(issue_number) if c.id == record.plan_comment_id]
    if posted != [record.plan_body_hash]:
        return False
    approval = server.get(f"/tasks/{require_safe_id(record.task_id)}/approval")
    return (approval["plan_digest"], _sha256(_findings_text(approval))) == (record.posted_digest, record.findings_hash)


def _approve_on_thumbs_up(github, server, state, state_dir, issue_number: int, record: IssueRecord, approver_ids: set[int]) -> None:
    """Reject on an approver's 👎 (which wins), else approve on their 👍; approvers match by numeric id, never login. `approved` is saved only after the server accepts."""
    reactions = github.reactions(record.plan_comment_id)
    if any(r.content == "-1" and r.user_id in approver_ids for r in reactions):
        server.post(f"/tasks/{require_safe_id(record.task_id)}/reject")
        _post_rejected(github, state, state_dir, issue_number, record)
        return
    if not any(r.content == "+1" and r.user_id in approver_ids for r in reactions):
        return
    if not _still_as_posted(github, server, issue_number, record):
        _post_changed(github, state, state_dir, issue_number, record)
        return
    try:
        server.post(f"/tasks/{require_safe_id(record.task_id)}/approve", {"plan_digest": record.posted_digest, "waive": []})
    except ServerRefused as refusal:
        if (refusal.status, refusal.error) != (409, "plan changed"):
            raise
        _post_changed(github, state, state_dir, issue_number, record)
        return
    record.phase = Phase.APPROVED
    save_state(state_dir, state)


_APPROVED_STATES = {"queued", "running", "agent_done", "verified"}  # the task was approved, here or at the terminal


def run_pass(github, server, state_dir, approver_ids: set[int], clock=_utc_now, home: Path | None = None) -> None:
    """One pass over the repo's issues: start a task for each new approver issue, post its plan once reviewed, approve it on an approver's 👍."""
    state = load_state(state_dir, clock)
    home = str(home or Path.home())
    for issue in parse_issues(github.open_issues(), approver_ids, state.watching_since):
        if issue.number not in state.issues:
            task_id = server.post("/tasks", {"goal": _goal(issue)})["id"]
            state.issues[issue.number] = IssueRecord(task_id)
            save_state(state_dir, state)
        record = state.issues[issue.number]
        if record.phase not in (Phase.NEW, Phase.AWAITING_APPROVAL, Phase.APPROVED):
            continue
        task = server.get(f"/tasks/{require_safe_id(record.task_id)}")
        if task["state"] == "failed":
            _post_final(github, state, state_dir, issue.number, record, Phase.FAILED, "Failure reason", strip_home(task["failure_reason"], home))
            continue
        if task["state"] in ("audited", "merged"):  # finished at the terminal; no steps to report
            record.phase = Phase.AUDITED
            save_state(state_dir, state)
            continue
        if task["state"] in _APPROVED_STATES:
            record.phase = Phase.APPROVED
            save_state(state_dir, state)
        if record.phase is Phase.AWAITING_APPROVAL:
            _approve_on_thumbs_up(github, server, state, state_dir, issue.number, record, approver_ids)
            continue
        result = _advance(server, record.task_id)
        if result is None:
            continue
        if result["stopped"]:
            _post_final(github, state, state_dir, issue.number, record, Phase.STOPPED, "Stopped", strip_home(result["stopped"], home))
        elif result["state"] == "reviewed" and record.phase is Phase.NEW:
            _post_plan(github, server, state, state_dir, issue.number, record)
        elif result["state"] == "audited" and record.phase is Phase.APPROVED:
            _post_audited(github, state, state_dir, issue.number, record, result)
        elif result["state"] == "failed":
            _post_final(github, state, state_dir, issue.number, record, Phase.FAILED, "Failure reason", strip_home(result["failure_reason"], home))
