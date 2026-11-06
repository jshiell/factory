from dataclasses import dataclass, replace
from typing import Protocol

from factory.classify import OutcomeKind, classify
from factory.command import RunnerConfig
from factory.ledger import Ledger
from factory.store import TaskStore
from factory.task import Event, Task, apply


ASSUMED_COST_MULTIPLIER = 2


class BudgetCapReached(Exception):
    pass


class MissingBudget(Exception):
    pass


@dataclass(frozen=True)
class RunResult:
    stdout: str
    exit_code: int
    timed_out: bool = False


class Runner(Protocol):
    def run(self, task: Task, config: RunnerConfig) -> RunResult: ...


def step(
    task_id: str,
    store: TaskStore,
    ledger: Ledger,
    runner: Runner,
    config: RunnerConfig,
    cap_usd: float,
) -> Task:
    running = apply(store.load(task_id), Event.START)
    if config.budget_usd is None:
        raise MissingBudget(task_id)
    if ledger.would_exceed(cap_usd, config.budget_usd):
        raise BudgetCapReached(task_id)
    store.save(running)
    try:
        result = runner.run(running, config)
    except Exception:
        return _fail_with_assumed_cost(running, "runner_error", store, ledger, config)
    if result.timed_out:
        return _fail_with_assumed_cost(running, "timeout", store, ledger, config)
    outcome = classify(result.stdout, result.exit_code)
    ledger.append(task_id, outcome.cost_usd)
    if outcome.kind in (OutcomeKind.SUCCESS, OutcomeKind.SUCCESS_WITH_DENIALS):
        finished = replace(apply(running, Event.SUCCEEDED), denials=outcome.denials)
    else:
        finished = apply(running, Event.FAILED, reason=outcome.kind.value)
    store.save(finished)
    return finished


def _fail_with_assumed_cost(
    running: Task, reason: str, store: TaskStore, ledger: Ledger, config: RunnerConfig
) -> Task:
    ledger.append(running.id, ASSUMED_COST_MULTIPLIER * config.budget_usd, assumed=True)
    failed = apply(running, Event.FAILED, reason=reason)
    store.save(failed)
    return failed
