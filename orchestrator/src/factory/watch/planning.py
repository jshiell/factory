from factory.answer_questions import MAX_ANSWER_BYTES
from factory.client import ServerRefused
from factory.comments import render_comment
from factory.ids import require_safe_id
from factory.question_text import questions_text
from factory.watch.posting import (
    _approver_reacted,
    _findings_text,
    _post_ending,
    _post_final,
    _sha256,
)
from factory.watch.progress import Progress
from factory.watch.state import IssueRecord, Phase
from factory.watch.thread import IssueThread

_HOW_TO_ACT = "Task {task_id}. React 👍 to approve this plan, 👎 to reject it."


def _post_plan(thread: IssueThread, server, progress: Progress) -> None:
    record = progress.record
    approval = server.get(f"/tasks/{require_safe_id(record.task_id)}/approval")
    findings = _findings_text(approval)
    required = "\n\n".join(filter(None, ["\n".join(approval["required"]), approval["warning"]]))
    body = render_comment(record.task_id, "plan", [
        ("Task", _HOW_TO_ACT.format(task_id=record.task_id), False),
        ("Plan", approval["plan"], True),
        ("Review findings", findings, True),
        ("Required review notes", required, True),
    ])
    exact_copy = thread.own_comment_id(lambda c: c.body == body)
    record.plan_comment_id = exact_copy or thread.comment(body)
    record.posted_digest = approval["plan_digest"]
    record.plan_body_hash = _sha256(body)
    record.findings_hash = _sha256(findings)
    record.phase = Phase.AWAITING_APPROVAL
    progress.save()


_HOW_TO_ANSWER = "Task {task_id}. Reply to this comment with your answers; the plan is written once you have answered."


def _post_questions(thread: IssueThread, server, progress: Progress) -> None:
    record = progress.record
    shown = server.get(f"/tasks/{require_safe_id(record.task_id)}/questions")
    body = render_comment(record.task_id, "questions", [
        ("Task", _HOW_TO_ANSWER.format(task_id=record.task_id), False),
        (f"Questions (round {shown['round']})", questions_text(shown["questions"]), True),
    ])
    exact_copy = thread.own_comment_id(lambda c: c.body == body)
    record.questions_comment_id = exact_copy or thread.comment(body)
    record.questions_round = shown["round"]
    record.phase = Phase.AWAITING_ANSWERS
    progress.save()


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


def _forward_answers(thread: IssueThread, server, progress: Progress, approver_ids: set[int]) -> bool:
    """Send the approvers' answers to the server; True when the wait is over."""
    record = progress.record
    answers = _answers_after(thread.comments(), record.questions_comment_id, approver_ids)
    if answers is None:
        text = f"{_QUESTIONS_GONE} Run: factory answer {require_safe_id(record.task_id)}"
        _post_final(thread, progress, Phase.STOPPED, "Stopped", text)
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
    progress.save()
    return True


_CHANGED = (
    "The plan comment, the plan or its review findings changed after the plan comment was posted, "
    "so no approval was made. Review the task at the terminal."
)


_REJECTED = "An approver rejected this plan, so the task is rejected and no work will be done."


def _post_changed(thread: IssueThread, progress: Progress) -> None:
    _post_ending(thread, progress, "changed", Phase.CHANGED, "Plan changed", _CHANGED)


def _post_rejected(thread: IssueThread, progress: Progress) -> None:
    _post_ending(thread, progress, "rejected", Phase.REJECTED, "Plan rejected", _REJECTED)


def _approval_if_as_posted(thread: IssueThread, server, record: IssueRecord) -> dict | None:
    """The server's approval preview, if the plan comment is unedited and the preview's plan digest and findings are the ones it showed."""
    posted = [_sha256(c.body) for c in thread.comments() if c.id == record.plan_comment_id]
    if posted != [record.plan_body_hash]:
        return None
    approval = server.get(f"/tasks/{require_safe_id(record.task_id)}/approval")
    if (approval["plan_digest"], _sha256(_findings_text(approval))) != (record.posted_digest, record.findings_hash):
        return None
    return approval


def _approve_on_thumbs_up(thread: IssueThread, server, progress: Progress, approver_ids: set[int]) -> None:
    """Reject on an approver's 👎 (which wins), else approve on their 👍; approvers match by numeric id, never login. `approved` is saved only after the server accepts."""
    record = progress.record
    reactions = thread.reactions(record.plan_comment_id)
    if _approver_reacted(reactions, "-1", approver_ids):
        server.post(f"/tasks/{require_safe_id(record.task_id)}/reject")
        _post_rejected(thread, progress)
        return
    if not _approver_reacted(reactions, "+1", approver_ids):
        return
    approval = _approval_if_as_posted(thread, server, record)
    if approval is None:
        _post_changed(thread, progress)
        return
    try:
        server.post(f"/tasks/{require_safe_id(record.task_id)}/approve", {
            "plan_digest": record.posted_digest, "review_findings": approval["review_findings"], "waive": []})
    except ServerRefused as refusal:
        if (refusal.status, refusal.error) != (409, "plan changed"):
            raise
        _post_changed(thread, progress)
        return
    record.phase = Phase.APPROVED
    progress.save()
