from factory.client import ServerRefused
from factory.comments import render_comment, strip_home
from factory.ids import require_safe_id
from factory.watch.posting import (
    _approver_reacted,
    _post_ending,
    _post_result,
    _result_comment_id,
)
from factory.watch.state import IssueRecord, Phase, save_state
from factory.watch.thread import IssueThread

_HOW_TO_RETRY = (
    "Task {task_id}. React 👍 to retry: a new task from the original goal, not the issue's current text, "
    "which plans and reviews again (this spends agent budget; the new plan needs its own 👍), 👎 to leave it."
)


def _post_failure(thread: IssueThread, state, state_dir, record: IssueRecord, reason: str, retry: bool) -> None:
    """The failure comment; with retry on, also the retry offer. Both are adopted or posted before one save, so a lost post leaves the issue for the next pass."""
    failure = render_comment(record.task_id, "result", [("Failure reason", reason, True)])
    if not retry:
        _post_result(thread.github, state, state_dir, thread.number, record, thread.self_id, "result", Phase.FAILED, failure)
        return
    offer = render_comment(record.task_id, "retry-offer", [("Retry", _HOW_TO_RETRY.format(task_id=record.task_id), False), ("Failure reason", reason, True)])
    failure_id = _result_comment_id(thread.github, thread.number, record, thread.self_id, "result", failure)
    record.retry_comment_id = _result_comment_id(thread.github, thread.number, record, thread.self_id, "retry-offer", offer)
    record.result_comment_id = failure_id
    record.phase = Phase.AWAITING_RETRY
    save_state(state_dir, state)


_NOT_RETRIED = "An approver declined the retry, so the task stays failed. Retry it at the terminal if you change your mind."


def _post_retry_refused(thread: IssueThread, state, state_dir, record: IssueRecord, reason: str) -> None:
    body = render_comment(record.task_id, "retry-refused", [
        ("Retry refused", reason, True),
        ("Next", f"Nothing was retried. At the terminal, run: factory retry {require_safe_id(record.task_id)} once the cause is fixed.", False),
    ])
    _post_result(thread.github, state, state_dir, thread.number, record, thread.self_id, "retry-refused", Phase.STOPPED, body)


def _retry_on_thumbs_up(thread: IssueThread, server, state, state_dir, record: IssueRecord, approver_ids: set[int], home: str) -> None:
    """Retry the failed task on an approver's 👍 on the offer and follow the new task from the next pass; approvers match by numeric id, never login."""
    reactions = thread.reactions(record.retry_comment_id)
    if _approver_reacted(reactions, "-1", approver_ids):
        _post_ending(thread.github, state, state_dir, thread.number, record, thread.self_id, "not-retried", Phase.NOT_MERGED, "Not retried", _NOT_RETRIED)
        return
    if not _approver_reacted(reactions, "+1", approver_ids):
        return
    try:
        new_id = server.post(f"/tasks/{require_safe_id(record.task_id)}/retry")["id"]
    except ServerRefused as refusal:
        if (refusal.status, refusal.error) != (409, "retry refused"):
            raise
        _post_retry_refused(thread, state, state_dir, record, strip_home(refusal.detail, home))
        return
    state.issues[thread.number] = IssueRecord(task_id=new_id)
    save_state(state_dir, state)
