from datetime import datetime
from collections.abc import Callable

from factory.approve_plan import build_implementation_prompt
from factory.audit_gaps import gaps_to_carry, split_quoting_waived
from factory.digest import plan_digest
from factory.review_notes import capped_findings
from factory.store import TaskStore, create_with_fresh_id
from factory.task import Task, TaskState
from factory.waiver import remove_waived

RETRYABLE = (TaskState.FAILED, TaskState.AUDITED)


class RetryRefused(Exception):
    pass


def retry_task(task_id: str, store: TaskStore, clock: Callable[[], datetime]) -> Task:
    task = store.load(task_id)
    if task.state not in RETRYABLE:
        raise RetryRefused(f"{task.id} is {task.state.value}; only failed or audited tasks can be retried")
    if not task.goal:
        raise RetryRefused(f"{task.id} has no goal to retry")
    return create_with_fresh_id(
        store, clock, lambda new_id: Task(id=new_id, prompt="", state=TaskState.PROPOSED, goal=task.goal, retry_of=task.id)
    )


def retry_with_plan(
    task_id: str, store: TaskStore, clock: Callable[[], datetime], shown_digest: str, shown_findings: tuple[str, ...],
    waived: tuple[str, ...] = (), shown_audit_gaps: tuple[str, ...] | None = None,
) -> Task:
    """Queue a new task with the plan a human already approved for `task_id` (the caller confirmed it with the human)."""
    task = store.load(task_id)
    require_keepable_plan(task, shown_digest)
    if capped_findings(shown_findings) != capped_findings(task.review_findings):
        raise RetryRefused(f"review findings mismatch for {task.id}")
    notes = remove_waived(capped_findings(task.review_findings), waived) if waived else task.review_findings
    gaps = gaps_to_carry(task)
    if shown_audit_gaps is not None and shown_audit_gaps != gaps:
        raise RetryRefused(f"audit gaps mismatch for {task.id}")
    gaps, _ = split_quoting_waived(gaps, waived)
    return create_with_fresh_id(
        store, clock,
        lambda new_id: Task(
            id=new_id, prompt=build_implementation_prompt(task.goal, task.plan, notes, gaps), state=TaskState.QUEUED, goal=task.goal,
            plan=task.plan, plan_risks=task.plan_risks, plan_digest=task.plan_digest,
            review_findings=notes, retry_of=task.id, waived_findings=task.waived_findings + waived,
            prior_audit_findings=gaps,
        ),
    )


def require_keepable_plan(task: Task, shown_digest: str) -> None:
    if task.state not in RETRYABLE:
        raise RetryRefused(f"{task.id} is {task.state.value}; only failed or audited tasks can be retried")
    if not task.goal:
        raise RetryRefused(f"{task.id} has no goal to retry")
    if not task.plan:
        raise RetryRefused(f"{task.id} has no plan to keep")
    if not (plan_digest(task.plan) == shown_digest == task.plan_digest):
        raise RetryRefused(f"plan digest mismatch for {task.id}")
    if not task.prompt:  # only approve_plan persists a prompt on a task that has a plan
        raise RetryRefused(f"{task.id}: its plan was never approved; use retry without --keep-plan")
