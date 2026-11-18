import hashlib

from factory.comments import has_marker, render_comment
from factory.watch.state import IssueRecord, Phase, save_state


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _findings_text(approval: dict) -> str:
    return "\n".join(f"- {finding}" for finding in approval["review_findings"])


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


def _post_ending(github, state, state_dir, issue_number: int, record: IssueRecord, self_id: int, kind: str, phase: Phase, heading: str, text: str) -> None:
    body = render_comment(record.task_id, kind, [(heading, text, False)])
    _post_result(github, state, state_dir, issue_number, record, self_id, kind, phase, body)


def _approver_reacted(reactions, content: str, approver_ids: set[int]) -> bool:
    """Approvers match by numeric id, never login."""
    return any(r.content == content and r.user_id in approver_ids for r in reactions)
