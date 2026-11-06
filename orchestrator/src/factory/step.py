from dataclasses import dataclass
from typing import Protocol

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
    runner.run(running, config)
    return running
