from factory.client import ServerRefused
from factory.comments import render_comment, strip_home
from factory.ids import require_safe_id
from factory.merge_task import ALREADY_IN_MAIN, MAIN_MOVED
from factory.watch.options import Options
from factory.watch.posting import (
    _approver_reacted,
    _post_ending,
    _post_result,
    _result_comment_id,
    _sha256,
)
from factory.watch.progress import Progress
from factory.watch.state import IssueRecord, Phase
from factory.watch.thread import IssueThread

_HOW_TO_MERGE = "Task {task_id}. React 👍 to merge, 👎 to leave it unmerged."


def _post_audited(thread: IssueThread, server, progress: Progress, result: dict, merge: bool) -> None:
    record = progress.record
    steps = "\n".join(f"{step['stage']} -> {step['state']}" for step in result["steps"])
    body = render_comment(record.task_id, "result", [
        ("Steps", steps, True),
        ("State", result["state"], True),
        ("Merge", f"When ready, run: factory merge {require_safe_id(record.task_id)}", False),
    ])
    if not merge:
        _post_result(thread, progress, "result", Phase.AUDITED, body)
        return
    record.result_comment_id = _result_comment_id(thread, record, "result", body)
    _post_merge_comment(thread, server, progress)


def _post_merge_refused(thread: IssueThread, progress: Progress, reason: str) -> None:
    record = progress.record
    task_id = require_safe_id(record.task_id)
    if reason in (ALREADY_IN_MAIN, _MERGE_JOB_ALREADY_IN_MAIN):
        next_step = "Nothing was merged by the factory: the audited commit is already in main. Check main, then close this issue."
    else:
        next_step = f"Nothing was merged. At the terminal, run: factory rebase {task_id} if main moved, or factory merge {task_id}."
    body = render_comment(record.task_id, "merge-refused", [
        ("Merge refused", reason, True),
        ("Next", next_step, False),
    ])
    _post_result(thread, progress, "merge-refused", Phase.MERGE_STOPPED, body)


_HOW_TO_REBASE = (
    "`main` moved since the audit. React 👍 to rebase the task onto `main` and re-audit it "
    "(this spends agent budget), 👎 to leave it."
)


def _post_merge_moved(thread: IssueThread, server, progress: Progress) -> None:
    """Offer the rebase. The body carries the audited commit, so no earlier round's offer (with its 👍) can match it."""
    record = progress.record
    audited_sha = server.get(f"/tasks/{require_safe_id(record.task_id)}")["audited_sha"]
    body = render_comment(record.task_id, "merge-moved", [
        ("Task", _HOW_TO_REBASE, False),
        ("Merge refused", MAIN_MOVED, True),
        ("Audited commit", audited_sha, True),
    ])
    exact_copy = thread.own_comment_id(lambda c: c.body == body)
    record.rebase_comment_id = exact_copy or thread.comment(body)
    record.rebase_sha = audited_sha
    record.phase = Phase.AWAITING_REBASE
    progress.save()


def _refuse_merge(thread: IssueThread, server, progress: Progress, reason: str) -> None:
    """Main having moved can be fixed by a rebase; any other refusal is for the terminal."""
    if reason == MAIN_MOVED:
        _post_merge_moved(thread, server, progress)
    else:
        _post_merge_refused(thread, progress, reason)


def _merge_preview_or_refuse(thread: IssueThread, server, progress: Progress) -> dict | None:
    """The server's merge preview; if the server refuses the merge, post the refusal and return None."""
    record = progress.record
    try:
        return server.get(f"/tasks/{require_safe_id(record.task_id)}/merge-preview")
    except ServerRefused as refusal:
        if refusal.error != "merge refused":
            raise
        _refuse_merge(thread, server, progress, refusal.detail)
        return None


def _post_merge_comment(thread: IssueThread, server, progress: Progress) -> None:
    record = progress.record
    preview = _merge_preview_or_refuse(thread, server, progress)
    if preview is None:
        return
    body = render_comment(record.task_id, "merge", [
        ("Task", _HOW_TO_MERGE.format(task_id=record.task_id), False),
        ("Audited commit", preview["audited_sha"], True),
        ("Branch", preview["branch"], True),
        ("Changes", preview["diffstat"], True),
        ("Audit findings", "\n".join(f"- {finding}" for finding in preview["findings"]), True),
    ])
    exact_copy = thread.own_comment_id(lambda c: c.body == body)
    record.merge_comment_id = exact_copy or thread.comment(body)
    record.merge_body_hash = _sha256(body)
    record.merge_sha = preview["audited_sha"]
    record.phase = Phase.AWAITING_MERGE
    progress.save()


_MERGED = "Merged. The commits are unsigned: sign them before pushing."


def _post_merged(thread: IssueThread, progress: Progress) -> None:
    _post_ending(thread, progress, "merged", Phase.MERGED, "Merged", _MERGED)


_NOT_MERGED = "An approver declined the merge, so nothing was merged. The task stays audited; merge it at the terminal if you change your mind."


_MERGE_CHANGED = (
    "The merge comment, or the audited commit it showed, changed after the comment was posted, "
    "so nothing was merged. Review the task at the terminal."
)


def _post_merge_changed(thread: IssueThread, progress: Progress) -> None:
    _post_ending(thread, progress, "merge-changed", Phase.MERGE_STOPPED, "Merge changed", _MERGE_CHANGED)


def _merge_comment_is_unedited(thread: IssueThread, record: IssueRecord) -> bool:
    posted = [_sha256(c.body) for c in thread.comments() if c.id == record.merge_comment_id]
    return posted == [record.merge_body_hash]


def _task_is_merged(server, task_id: str) -> bool:
    return server.get(f"/tasks/{task_id}")["state"] == "merged"


_MERGE_JOB_MAIN_MOVED = f"merge refused: {MAIN_MOVED}"  # the error a failed merge job carries


_MERGE_JOB_ALREADY_IN_MAIN = f"merge refused: {ALREADY_IN_MAIN}"


def _merge_on_thumbs_up(thread: IssueThread, server, progress: Progress, options: Options) -> None:
    """Merge the commit the merge comment showed on an approver's 👍; approvers match by numeric id, never login."""
    record = progress.record
    reactions = thread.reactions(record.merge_comment_id)
    if _approver_reacted(reactions, "-1", options.approver_ids):
        _post_ending(thread, progress, "not-merged", Phase.NOT_MERGED, "Not merged", _NOT_MERGED)
        return
    if not _approver_reacted(reactions, "+1", options.approver_ids):
        return
    task_id =require_safe_id(record.task_id)
    if not _merge_comment_is_unedited(thread, record):
        _post_merge_changed(thread, progress)
        return
    preview = _merge_preview_or_refuse(thread, server, progress)
    if preview is None:
        return
    if preview["audited_sha"] != record.merge_sha:
        _post_merge_changed(thread, progress)
        return
    job_id = server.post(f"/tasks/{task_id}/merge", {"audited_sha": record.merge_sha})["job"]["id"]
    job = server.wait(job_id)
    if job["state"] == "succeeded" or _task_is_merged(server, task_id):
        _post_merged(thread, progress)
    elif job["state"] == "failed" and job["error"] == _MERGE_JOB_MAIN_MOVED:
        _post_merge_moved(thread, server, progress)
    elif job["state"] == "failed":
        _post_merge_refused(thread, progress, job["error"])


_NOT_REBASED = "An approver declined the rebase, so nothing was rebased or merged. The task stays audited; rebase it at the terminal if you change your mind."


def _post_rebase_refused(thread: IssueThread, progress: Progress, reason: str) -> None:
    record = progress.record
    task_id = require_safe_id(record.task_id)
    body = render_comment(record.task_id, "rebase-refused", [
        ("Rebase refused", reason, True),
        ("Next", f"Nothing was rebased. At the terminal, run: factory rebase {task_id} once the cause is fixed.", False),
    ])
    _post_result(thread, progress, "rebase-refused", Phase.MERGE_STOPPED, body)


def _task_is_rebased(server, record: IssueRecord) -> bool:
    """A job that failed after the rebase landed (a lost reply, a rebase at the terminal): the task has left the offered commit."""
    task = server.get(f"/tasks/{require_safe_id(record.task_id)}")
    return task["state"] == "agent_done" or task["audited_sha"] != record.rebase_sha


def _rebase_on_thumbs_up(thread: IssueThread, server, progress: Progress, task: dict, options: Options) -> None:
    """Rebase the task on an approver's 👍 on the rebase offer, if it is still at the commit the offer showed; approvers match by numeric id, never login."""
    record = progress.record
    reactions = thread.reactions(record.rebase_comment_id)
    if _approver_reacted(reactions, "-1", options.approver_ids):
        _post_ending(thread, progress, "not-merged", Phase.NOT_MERGED, "Not rebased", _NOT_REBASED)
        return
    if not _approver_reacted(reactions, "+1", options.approver_ids):
        return
    if task["audited_sha"] == record.rebase_sha:
        job_id = server.post(f"/tasks/{require_safe_id(record.task_id)}/rebase")["job"]["id"]
        job = server.wait(job_id)
        if job["state"] == "failed" and not _task_is_rebased(server, record):
            _post_rebase_refused(thread, progress, strip_home(job["error"], options.home))
            return
        if job["state"] not in ("succeeded", "failed"):
            return
    record.phase = Phase.APPROVED
    progress.save()
