from dataclasses import dataclass, replace
from typing import Protocol

from factory.classify import OutcomeKind, classify
from factory.command import RunnerConfig
from factory.ledger import Ledger
from factory.store import TaskStore
from factory.task import Event, Task, apply


@dataclass(frozen=True)
class RunResult:
    stdout: str
    exit_code: int


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
    store.save(running)
    result = runner.run(running, config)
    outcome = classify(result.stdout, result.exit_code)
    ledger.append(task_id, outcome.cost_usd)
    if outcome.kind in (OutcomeKind.SUCCESS, OutcomeKind.SUCCESS_WITH_DENIALS):
        finished = replace(apply(running, Event.SUCCEEDED), denials=outcome.denials)
    else:
        finished = apply(running, Event.FAILED, reason=outcome.kind.value)
    store.save(finished)
    return finished
