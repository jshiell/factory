import logging
from pathlib import Path

import pytest

from factory.command import RunnerConfig
from factory.ledger import Ledger
from factory.step import BudgetCapReached, MissingBudget, RunResult, step
from factory.store import TaskStore
from factory.task import InvalidTransition, Task, TaskState

CONFIG = RunnerConfig(model="sonnet", budget_usd=0.5)
FIXTURES = Path(__file__).parent / "fixtures"


def fixture_stdout(name: str) -> str:
    return (FIXTURES / f"{name}.json").read_text()


class FakeRunner:
    def __init__(self, stdout: str = "", exit_code: int = 0, on_run=None, timed_out: bool = False):
        self._result = RunResult(stdout, exit_code, timed_out)
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


def test_success_with_denials_is_agent_done_and_records_denials(store, ledger):
    runner = FakeRunner(fixture_stdout("denials"))

    task = step("t1", store, ledger, runner, CONFIG, cap_usd=10)

    assert task.state is TaskState.AGENT_DONE
    assert len(task.denials) == 5
    assert store.load("t1") == task


def test_cap_that_would_be_exceeded_refuses_without_calling_runner(store, ledger):
    ledger.append(task_id="earlier", cost_usd=9.8)
    runner = FakeRunner(fixture_stdout("success"))

    with pytest.raises(BudgetCapReached):
        step("t1", store, ledger, runner, CONFIG, cap_usd=10)

    assert runner.calls == 0
    assert store.load("t1").state is TaskState.QUEUED


def test_non_queued_task_is_rejected_without_calling_runner(store, ledger):
    store.save(Task(id="t1", prompt="done already", state=TaskState.AGENT_DONE))
    runner = FakeRunner(fixture_stdout("success"))

    with pytest.raises(InvalidTransition):
        step("t1", store, ledger, runner, CONFIG, cap_usd=10)

    assert runner.calls == 0


def test_missing_budget_is_refused_without_calling_runner(store, ledger):
    runner = FakeRunner(fixture_stdout("success"))

    with pytest.raises(MissingBudget):
        step("t1", store, ledger, runner, RunnerConfig(model="sonnet"), cap_usd=10)

    assert runner.calls == 0
    assert store.load("t1").state is TaskState.QUEUED


def test_non_queued_task_without_budget_is_invalid_transition_first(store, ledger):
    store.save(Task(id="t1", prompt="done", state=TaskState.AGENT_DONE))

    with pytest.raises(InvalidTransition):
        step("t1", store, ledger, FakeRunner(), RunnerConfig(model="sonnet"), cap_usd=10)


def test_run_result_defaults_to_not_timed_out():
    assert RunResult("", 0).timed_out is False


def test_timeout_fails_the_task_and_records_an_assumed_double_budget_cost(store, ledger):
    runner = FakeRunner("", -9, timed_out=True)

    task = step("t1", store, ledger, runner, CONFIG, cap_usd=10)

    assert task.state is TaskState.FAILED
    assert task.failure_reason == "timeout"
    assert ledger.total() == 1.0
    assert ledger.entries()[0]["assumed"] is True


def test_a_runner_exception_in_step_is_logged_with_its_text(store, ledger, caplog):
    def explode(task):
        raise FileNotFoundError("no such cwd: /w/target")

    with caplog.at_level(logging.WARNING, logger="factory"):
        step("t1", store, ledger, FakeRunner(on_run=explode), CONFIG, cap_usd=10)

    assert any("t1" in r.getMessage() and "implement" in r.getMessage() and "no such cwd: /w/target" in r.getMessage() for r in caplog.records)


def test_runner_exception_fails_the_task_instead_of_leaving_it_running(store, ledger):
    def explode(task):
        raise FileNotFoundError("no claude")

    runner = FakeRunner(on_run=explode)

    task = step("t1", store, ledger, runner, CONFIG, cap_usd=10)

    assert task.state is TaskState.FAILED
    assert task.failure_reason == "runner_error"
    assert store.load("t1").state is TaskState.FAILED
    assert ledger.total() == 1.0


def test_implementer_cost_is_recorded_under_the_implement_stage(store, ledger):
    step("t1", store, ledger, FakeRunner(fixture_stdout("success")), CONFIG, cap_usd=10)

    assert ledger.entries()[0]["stage"] == "implement"


def test_assumed_implementer_cost_is_also_staged(store, ledger):
    step("t1", store, ledger, FakeRunner(timed_out=True), CONFIG, cap_usd=10)

    assert ledger.entries()[0]["stage"] == "implement"
