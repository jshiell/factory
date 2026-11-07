from dataclasses import replace

from factory.command import RunnerConfig
from factory.digest import plan_digest
from factory.ledger import Ledger
from factory.readonly_stage import Infra, Modified, run_readonly_stage
from factory.step import Runner, check_budget
from factory.store import TaskStore
from factory.task import Event, Task, apply

MAX_PLAN_BYTES = 100_000

PLAN_SCHEMA = {
    "type": "object",
    "required": ["plan", "risks"],
    "additionalProperties": False,
    "properties": {
        "plan": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
}

PLAN_CONFIG = RunnerConfig(
    model="sonnet",
    budget_usd=0.30,
    disallowed_tools=("Write", "Edit", "NotebookEdit", "Bash", "Glob"),
    confine=True,
    json_schema=PLAN_SCHEMA,
    max_turns=8,
    append_system_prompt="You are a read-only planner. Never try to modify files.",
)


def build_plan_prompt(goal: str) -> str:
    return (
        "Write an implementation plan for this goal in the project in the current directory. "
        "Explore with the read tools first and name real files. Plan test-first: add tests, never edit "
        f"or remove existing ones, and do not change test configuration.\n\nGOAL: {goal}"
    )


def plan_task(
    task_id: str,
    store: TaskStore,
    ledger: Ledger,
    runner: Runner,
    config: RunnerConfig,
    cap_usd: float,
    worktrees,
) -> Task:
    task = store.load(task_id)
    apply(task, Event.PLAN_DONE)  # reject tasks that are not proposed before any other work
    check_budget(task_id, ledger, config, cap_usd)
    if task.worktree is None:
        path, base_sha = worktrees.create(task_id)
        task = replace(task, worktree=str(path), base_sha=base_sha)
        store.save(task)
    staged = run_readonly_stage(task, build_plan_prompt(task.goal), runner, config, ledger, "plan")
    if isinstance(staged, Modified):
        failed = apply(task, Event.PLAN_FAILED, "plan_modified_worktree")
        return _finish(store, replace(failed, plan_error=None))
    if isinstance(staged, Infra):
        return _finish(store, replace(task, plan_error=staged.reason))
    parsed = _parse_plan(staged.structured_output)
    if isinstance(parsed, str):
        return _finish(store, replace(task, plan_error=parsed))
    plan, risks = parsed
    planned = apply(task, Event.PLAN_DONE)
    return _finish(
        store,
        replace(planned, plan=plan, plan_risks=risks, plan_digest=plan_digest(plan), plan_error=None),
    )


def _parse_plan(output) -> tuple[str, tuple[str, ...]] | str:
    """Return (plan, risks), or the plan_error reason."""
    if not isinstance(output, dict):
        return "plan_malformed"
    plan, risks = output.get("plan"), output.get("risks")
    if not isinstance(plan, str) or not plan.strip():
        return "plan_malformed"
    if not isinstance(risks, list) or not all(isinstance(r, str) for r in risks):
        return "plan_malformed"
    if len(plan.encode()) > MAX_PLAN_BYTES:
        return "plan_too_large"
    return plan, tuple(risks)


def _finish(store: TaskStore, task: Task) -> Task:
    store.save(task)
    return task
