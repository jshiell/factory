"""Task actions shared by the JSON API and the web UI: queue a job, create a task, retry one."""

from factory import service
from factory.abandon_task import AbandonRefused, abandon_task
from factory.advance import advance
from factory.audit_gaps import gaps_to_carry
from factory.digest import plan_digest
from factory.merge_task import MergeRefused, Preview, merge_preview
from factory.rebase_task import RebaseRefused
from factory.recover_task import RecoverRefused, recover_task
from factory.retry_task import require_keepable_plan, retry_task
from factory.task import InvalidTransition, Task
from factory.worktrees import WorktreeManager

# What the web pages and the JSON API tell a caller when git or the filesystem could not run a check: never the git
# command line or a path.
MERGE_CHECK_FAILED = "Git could not check this task's branch and worktree, so it cannot be merged."
CLEAN_CHECK_FAILED = "Git could not check the repository, so nothing can be cleaned."


class InvalidInput(Exception):
    """The caller's input cannot make a task."""


class ShownCommitChanged(Exception):
    """The commit the caller was shown is not the task's audited commit."""


class CleanChanged(Exception):
    """What would be cleaned is no longer what the caller was shown."""


def _refusal_as(prefix: str, refused: type[Exception] | tuple[type[Exception], ...], run):
    """Run `run`, turning the domain's refusal into the CLI's `<prefix> refused: <reason>` message."""
    def wrapped():
        try:
            return run()
        except refused as error:
            raise RuntimeError(f"{prefix} refused: {error}") from None
    return wrapped


def _advance_result_json(result) -> dict:
    task = result.task
    payload = {"steps": [{"stage": step.stage, "state": step.state.value} for step in result.steps],
               "state": task.state.value, "failure_reason": task.failure_reason, "stopped": result.stopped}
    if result.stopped_error:  # present only when there is one, so payloads without it are unchanged
        payload["stopped_error"] = {"field": result.stopped_error.field, "code": result.stopped_error.code}
    return payload


def _task_result_json(task) -> dict:
    return {"state": task.state.value, "failure_reason": task.failure_reason}


class WebActions:
    def __init__(self, ctx, runner, *, merge, rebase, retry_with_plan):
        """`merge`, `rebase` and `retry_with_plan` are the domain functions, wired by the server (its tests and
        tripwires pin where merging and rebasing are called from)."""
        self._ctx = ctx
        self._runner = runner
        self._merge, self._rebase, self._retry_with_plan = merge, rebase, retry_with_plan

    def _enqueue(self, kind: str, task_id: str, refused, run_for, to_json=_task_result_json):
        """Queue `kind` for the task (unknown task raises now); the domain's refusal ends the job as failed."""
        service.get_task(self._ctx.store, task_id)
        run = _refusal_as(kind, refused, lambda: to_json(run_for(task_id)))
        return self._runner.submit(kind, task_id, run)

    def enqueue_advance(self, task_id: str):
        service.get_task(self._ctx.store, task_id)
        return self._runner.submit(
            "advance", task_id, lambda: _advance_result_json(advance(task_id, self._ctx.store, self._ctx.stages)))

    def enqueue_abandon(self, task_id: str):
        return self._enqueue("abandon", task_id, AbandonRefused, lambda id_: abandon_task(id_, self._ctx.store))

    def enqueue_recover(self, task_id: str):
        ctx = self._ctx
        return self._enqueue("recover", task_id, RecoverRefused, lambda id_: recover_task(
            id_, ctx.store, ctx.ledger, ctx.settings.budget_usd))

    def enqueue_rebase(self, task_id: str):
        ctx = self._ctx
        return self._enqueue(
            "rebase", task_id, (RebaseRefused, InvalidTransition),
            lambda id_: self._rebase(id_, ctx.store, ctx.settings.repo, ctx.settings.main_branch),
            lambda rebased: {**_task_result_json(rebased.task), "old_audited_sha": rebased.old_audited_sha})

    def merge_preview(self, task_id: str) -> Preview:
        """What a merge would do, checked against git now; raises `MergeRefused`, `InvalidTransition`,
        `CalledProcessError` and `OSError` (including `WorktreeMissing`) from the domain."""
        settings = self._ctx.settings
        return merge_preview(task_id, self._ctx.store, settings.repo, settings.main_branch)

    def enqueue_merge(self, task_id: str, shown_sha: str):
        """Queue the merge of the commit the caller was shown; raises `ShownCommitChanged` if it is not the audited one."""
        ctx = self._ctx
        if shown_sha != service.get_task(ctx.store, task_id).audited_sha:
            raise ShownCommitChanged("the commit shown is not the audited commit")

        def merge_now(id_):
            worktrees = WorktreeManager(ctx.settings.repo, ctx.settings.worktree_root)
            return self._merge(id_, ctx.store, ctx.settings.repo, worktrees, shown_sha=shown_sha,
                              main_branch=ctx.settings.main_branch)

        return self._enqueue("merge", task_id, (MergeRefused, InvalidTransition), merge_now)

    def create(self, goal: str, grill) -> str:
        """Create a task and return its id; raises `InvalidInput` for a goal or grill flag no task can carry."""
        self._require_valid_goal(goal)
        if not isinstance(grill, bool):
            raise InvalidInput("grill must be true or false")
        return service.create_task(self._ctx.store, self._ctx.clock, goal, grill).id

    @staticmethod
    def _require_valid_goal(goal: str) -> None:
        try:
            goal_bytes = goal.encode()
        except UnicodeEncodeError:  # JSON admits lone surrogates, which no argv string can carry
            raise InvalidInput("goal must be valid text") from None
        if len(goal_bytes) > service.MAX_GOAL_BYTES:
            raise InvalidInput(f"goal must be at most {service.MAX_GOAL_BYTES} bytes")

    def keepable_plan(self, task_id: str) -> Task:
        """The task, loaded now, if a retry can keep its plan; raises `RetryRefused` if it cannot."""
        task = service.get_task(self._ctx.store, task_id)
        require_keepable_plan(task, plan_digest(task.plan or ""))
        return task

    def retry(self, task_id: str, plan_digest: str | None = None, waive=(), reinstate=()) -> str:
        """Retry a task and return the new task's id; with `plan_digest` the new task keeps the shown plan.

        Raises `RetryRefused` and `InvalidWaiver` from the domain."""
        ctx = self._ctx
        service.get_task(ctx.store, task_id)
        if plan_digest is None:
            return retry_task(task_id, ctx.store, ctx.clock).id
        task = ctx.store.load(task_id)
        optional = {"waived": waive, "shown_audit_gaps": gaps_to_carry(task), "reinstated": reinstate}
        return self._retry_with_plan(
            task_id, ctx.store, ctx.clock, plan_digest, shown_findings=task.review_findings,
            **{name: value for name, value in optional.items() if value}).id
