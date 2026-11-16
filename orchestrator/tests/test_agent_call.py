import logging

import pytest

from factory.agent_call import Infra, call_agent
from factory.command import RunnerConfig
from factory.ledger import Ledger
from factory.step import RunResult
from factory.task import Task

CONFIG = RunnerConfig(model="sonnet", budget_usd=0.5)


class RaisingRunner:
    def run(self, task, config):
        raise FileNotFoundError("no such cwd: /w/target")


class FixedRunner:
    def __init__(self, result):
        self._result = result

    def run(self, task, config):
        return self._result


def run_logged(tmp_path, caplog, result, stage="plan"):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    with caplog.at_level(logging.WARNING, logger="factory"):
        call_agent(Task(id="t1", prompt="p"), "prompt", FixedRunner(result), CONFIG, ledger, stage)
    return caplog.records


def test_an_unparseable_result_logs_the_exit_code_and_both_output_tails(tmp_path, caplog):
    records = run_logged(tmp_path, caplog, RunResult("not json", 1, stderr="boom"), stage="review")

    assert len(records) == 1
    message = records[0].getMessage()
    assert all(text in message for text in ("t1", "review", "unparseable", "not json", "boom"))
    assert "exit code 1" in message


@pytest.mark.parametrize(
    "stdout",
    [
        '{"type": "result", "subtype": "success", "total_cost_usd": 0.1}',
        '{"type": "result", "subtype": "error_during_execution", "is_error": true}',
        '{"type": "result", "subtype": "error_max_budget_usd"}',
    ],
)
def test_a_parseable_result_logs_nothing(tmp_path, caplog, stdout):
    assert run_logged(tmp_path, caplog, RunResult(stdout, 1, stderr="boom")) == []


def test_a_timeout_logs_nothing(tmp_path, caplog):
    assert run_logged(tmp_path, caplog, RunResult("", None, timed_out=True, stderr="boom")) == []


def test_a_runner_exception_is_logged_with_its_text_for_the_stage(tmp_path, caplog):
    ledger = Ledger(tmp_path / "ledger.jsonl")

    with caplog.at_level(logging.WARNING, logger="factory"):
        result = call_agent(Task(id="t1", prompt="p"), "prompt", RaisingRunner(), CONFIG, ledger, "plan")

    assert result == Infra("runner_error")
    assert any("t1" in r.getMessage() and "plan" in r.getMessage() and "no such cwd: /w/target" in r.getMessage() for r in caplog.records)
