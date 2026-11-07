from collections.abc import Callable
from dataclasses import dataclass

from factory.audit_task import AUDIT_CONFIG, audit_task
from factory.command import RunnerConfig
from factory.ledger import Ledger
from factory.plan_task import PLAN_CONFIG, plan_task
from factory.review_plan import REVIEW_CONFIG, review_plan
from factory.run_task import run_task
from factory.settings import Settings
from factory.step import Runner
from factory.store import TaskStore
from factory.task import Task
from factory.verify_task import verify_task


@dataclass(frozen=True)
class Deps:
    store: TaskStore
    ledger: Ledger
    runner: Runner
    worktrees: object  # anything with create(task_id) -> (path, base_sha)


def implementer_config(settings: Settings) -> RunnerConfig:
    return RunnerConfig(
        model=settings.model,
        budget_usd=settings.budget_usd,
        permission_mode="acceptEdits",
        allowed_tools=settings.allowed_tools,
        disallowed_tools=("Bash", "Bash(git *)", "Glob", "NotebookEdit"),  # deny wins over any user allow rule
        confine=True,
        max_turns=settings.max_turns,
        append_system_prompt="You are in a git worktree; the project lives in the current directory. Do not commit.",
    )


def build_stages(settings: Settings, deps: Deps) -> dict[str, Callable[[str], Task]]:
    store, ledger, runner, cap = deps.store, deps.ledger, deps.runner, settings.cap_usd
    implementer = implementer_config(settings)
    return {
        "plan": lambda task_id: plan_task(task_id, store, ledger, runner, PLAN_CONFIG, cap, deps.worktrees),
        "review": lambda task_id: review_plan(task_id, store, ledger, runner, REVIEW_CONFIG, cap),
        "implement": lambda task_id: run_task(task_id, store, ledger, runner, implementer, cap, deps.worktrees),
        "verify": lambda task_id: verify_task(
            task_id, store, project_subdir=settings.project_subdir,
            test_argv=list(settings.test_argv), gate_timeout_s=settings.gate_timeout_s,
        ),
        "audit": lambda task_id: audit_task(
            task_id, store, ledger, runner, AUDIT_CONFIG, cap, settings.project_subdir
        ),
    }
