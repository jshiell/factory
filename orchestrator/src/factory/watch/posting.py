import hashlib

from factory.comments import has_marker, render_comment
from factory.watch.state import IssueRecord, Phase, save_state
from factory.watch.thread import IssueThread


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _findings_text(approval: dict) -> str:
    return "\n".join(f"- {finding}" for finding in approval["review_findings"])


def _result_comment_id(thread: IssueThread, record: IssueRecord, kind: str, body: str) -> int:
    """Post the task's ending, or adopt this watcher's earlier one; nothing downstream reads its text, so the marker alone identifies it."""
    earlier = thread.own_comment_id(lambda c: has_marker(c.body, record.task_id, kind))
    return earlier or thread.comment(body)


def _post_result(thread: IssueThread, state, state_dir, record: IssueRecord, kind: str, phase: Phase, body: str) -> None:
    record.result_comment_id = _result_comment_id(thread, record, kind, body)
    record.phase = phase
    save_state(state_dir, state)


def _post_final(thread: IssueThread, state, state_dir, record: IssueRecord, phase: Phase, heading: str, text: str) -> None:
    body = render_comment(record.task_id, "result", [(heading, text, True)])
    _post_result(thread, state, state_dir, record, "result", phase, body)


def _post_ending(thread: IssueThread, state, state_dir, record: IssueRecord, kind: str, phase: Phase, heading: str, text: str) -> None:
    body = render_comment(record.task_id, kind, [(heading, text, False)])
    _post_result(thread, state, state_dir, record, kind, phase, body)


def _approver_reacted(reactions, content: str, approver_ids: set[int]) -> bool:
    """Approvers match by numeric id, never login."""
    return any(r.content == content and r.user_id in approver_ids for r in reactions)
