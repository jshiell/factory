import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


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


def run_pass(github, server, state_dir, approver_ids: set[int], clock=_utc_now) -> None:
    """One pass over the repo's issues; so far it only starts a task for each new approver issue."""
    state = load_state(state_dir, clock)
    for issue in parse_issues(github.open_issues(), approver_ids, state.watching_since):
        if issue.number not in state.issues:
            task_id = server.post("/tasks", {"goal": _goal(issue)})["id"]
            state.issues[issue.number] = IssueRecord(task_id)
            save_state(state_dir, state)
