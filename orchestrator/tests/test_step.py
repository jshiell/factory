from pathlib import Path

import pytest

from factory.command import RunnerConfig
from factory.ledger import Ledger
from factory.step import RunResult, step
from factory.store import TaskStore
from factory.task import Task, TaskState

CONFIG = RunnerConfig(model="sonnet", budget_usd=0.5)
FIXTURES = Path(__file__).parent / "fixtures"


def fixture_stdout(name: str) -> str:
    return (FIXTURES / f"{name}.json").read_text()


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


def test_success_marks_agent_done_and_records_cost(store, ledger):
    runner = FakeRunner(fixture_stdout("success"))

    task = step("t1", store, ledger, runner, CONFIG, cap_usd=10)

    assert task.state is TaskState.AGENT_DONE
    assert store.load("t1").state is TaskState.AGENT_DONE
    assert ledger.total() == 0.047295500000000004


@pytest.mark.parametrize(
    ("stdout", "exit_code", "expected_reason", "expected_cost"),
    [
        (fixture_stdout("budget"), 1, "budget_exhausted", 0.0689462),
        ("", 1, "unparseable", 0.0),
        (
            '{"type": "result", "subtype": "error_during_execution", "is_error": true, "total_cost_usd": 0.01}',
            1,
            "error",
            0.01,
        ),
    ],
)
def test_non_success_fails_the_task_and_still_records_cost(
    store, ledger, stdout, exit_code, expected_reason, expected_cost
):
    runner = FakeRunner(stdout, exit_code)

    task = step("t1", store, ledger, runner, CONFIG, cap_usd=10)

    assert task.state is TaskState.FAILED
    assert task.failure_reason == expected_reason
    assert store.load("t1") == task
    assert ledger.total() == expected_cost
