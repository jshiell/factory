import json

import pytest

from factory.command import RunnerConfig
from factory.ledger import Ledger
from factory.step import RunResult, step
from factory.store import TaskStore
from factory.task import Task, TaskState

CONFIG = RunnerConfig(model="sonnet", budget_usd=0.5)


class FakeRunner:
    def __init__(self, stdout: str = "", exit_code: int = 0, on_run=None):
        self._result = RunResult(stdout, exit_code)
        self._on_run = on_run
        self.calls = 0

    def run(self, task: Task, config: RunnerConfig) -> RunResult:
        self.calls += 1
        if self._on_run:
            self._on_run(task)
        return self._result


@pytest.fixture
def store(tmp_path):
    store = TaskStore(tmp_path / "tasks")
    (tmp_path / "tasks").mkdir()
    store.save(Task(id="t1", prompt="do it"))
    return store


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "ledger.jsonl")


def test_running_state_is_persisted_before_the_runner_is_called(store, ledger):
    seen = []
    runner = FakeRunner(on_run=lambda task: seen.append(store.load("t1").state))

    step("t1", store, ledger, runner, CONFIG, cap_usd=10)

    assert seen == [TaskState.RUNNING]
