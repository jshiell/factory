from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from factory.agent_call import Infra, call_agent
from factory.classify import OutcomeKind
from factory.command import RunnerConfig
from factory.fix_prompt import OUTPUT_LIMIT, build_fix_prompt
from factory.gate import GateResult, check_gate, commit_all, has_nested_repo
from factory.gate_sandbox import GateSandbox
from factory.ledger import Ledger
from factory.step import ASSUMED_COST_MULTIPLIER, Runner, check_budget
from factory.store import TaskStore
from factory.task import Event, Task, apply
from factory.worktrees import run_git

_SUCCESS = (OutcomeKind.SUCCESS, OutcomeKind.SUCCESS_WITH_DENIALS)
FIX_STAGE = "fix"


def verify_task(
    task_id: str,
    store: TaskStore,
    project_subdir: str,
    test_argv: list[str],
    gate_timeout_s: float,
    sandbox: Callable[[Task], GateSandbox] | None = None,
    *,
    runner: Runner | None = None,
    ledger: Ledger | None = None,
    fix_config: RunnerConfig | None = None,
    cap_usd: float | None = None,
    fix_rounds: int = 0,
) -> Task:
    """Gate the agent's work; on `tests_failed`, up to `fix_rounds` fresh agent runs get the failure text."""
    task = store.load(task_id)
    apply(task, Event.GATE_PASSED)  # reject tasks that are not agent_done before touching git
    if task.fix_in_flight:
        return _fail_interrupted(task, store, ledger, fix_config)
    capture = OUTPUT_LIMIT if fix_rounds > 0 else None
    while True:
        result = _gate_once(task, project_subdir, test_argv, gate_timeout_s, sandbox, capture)
        if result.passed:
            return _finish(apply(task, Event.GATE_PASSED), store)
        if result.reason != "tests_failed" or task.fix_rounds_used >= fix_rounds:
            return _finish(apply(task, Event.GATE_FAILED, reason=result.reason), store)
        task, failure = _fix_round(task, result.output or "", store, runner, ledger, fix_config, cap_usd)
        if failure is not None:
            return _finish(apply(task, Event.GATE_FAILED, reason=failure), store)


def _fix_round(
    task: Task, test_output: str, store: TaskStore, runner: Runner, ledger: Ledger, config: RunnerConfig, cap_usd: float
) -> tuple[Task, str | None]:
    """One agent run. Returns the updated task and a failure reason, or None when the gate should run again."""
    check_budget(task.id, ledger, config, cap_usd)  # raises with the task untouched: raise the cap and re-advance
    worktree = Path(task.worktree)
    head = run_git(worktree, "rev-parse", "HEAD")
    task = replace(task, fix_rounds_used=task.fix_rounds_used + 1, fix_in_flight=True)
    store.save(task)  # before the run: a crash cannot reset the counter, and the marker flags a possible orphan
    called = call_agent(task, build_fix_prompt(task, test_output), runner, config, ledger, FIX_STAGE)
    task = replace(task, fix_in_flight=False)
    if isinstance(called, Infra):
        store.save(task)
        return task, f"fix_{called.reason}"
    task = replace(task, denials=task.denials + called.outcome.denials)
    store.save(task)
    if called.outcome.kind not in _SUCCESS:
        return task, f"fix_{called.outcome.kind.value}"
    if run_git(worktree, "rev-parse", "HEAD") != head:
        return task, "fix_moved"
    return task, None


def _fail_interrupted(task: Task, store: TaskStore, ledger: Ledger | None, config: RunnerConfig | None) -> Task:
    """A driver died while a fix agent ran: it may still be alive, so touch no git and charge the assumed cost."""
    if ledger is not None and config is not None and config.budget_usd is not None:
        ledger.append(task.id, ASSUMED_COST_MULTIPLIER * config.budget_usd, assumed=True, stage=FIX_STAGE)
    return _finish(apply(replace(task, fix_in_flight=False), Event.GATE_FAILED, reason="fix_interrupted"), store)


def _finish(task: Task, store: TaskStore) -> Task:
    store.save(task)
    return task


def _gate_once(
    task: Task, project_subdir: str, test_argv: list[str], gate_timeout_s: float,
    sandbox: Callable[[Task], GateSandbox] | None, capture_limit: int | None = None,
) -> GateResult:
    worktree = Path(task.worktree)
    if has_nested_repo(worktree, project_subdir):  # before `git add -A` records it as a gitlink
        return GateResult(False, "nested_repo")
    commit_all(worktree, f"Task {task.id}")
    if sandbox is None:
        return check_gate(worktree, task.base_sha, project_subdir, test_argv, gate_timeout_s, capture_limit=capture_limit)
    confined = sandbox(task)
    return check_gate(
        worktree, task.base_sha, project_subdir, list(confined.argv), gate_timeout_s,
        env=confined.env, test_prefix=confined.prefix, capture_limit=capture_limit,
    )
