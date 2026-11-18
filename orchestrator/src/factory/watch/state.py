import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path


class Phase(StrEnum):
    NEW = "new"
    AWAITING_ANSWERS = "awaiting_answers"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    AUDITED = "audited"
    AWAITING_MERGE = "awaiting_merge"
    AWAITING_REBASE = "awaiting_rebase"
    AWAITING_RETRY = "awaiting_retry"
    MERGED = "merged"
    MERGE_STOPPED = "merge_stopped"
    NOT_MERGED = "not_merged"
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
    questions_comment_id: int | None = None
    questions_round: int | None = None
    merge_comment_id: int | None = None
    merge_body_hash: str | None = None
    merge_sha: str | None = None
    rebase_comment_id: int | None = None
    rebase_sha: str | None = None
    retry_comment_id: int | None = None


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
    return datetime.now(UTC)


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
