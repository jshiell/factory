import logging

from factory.agent_call import Infra, call_agent
from factory.command import RunnerConfig
from factory.ledger import Ledger
from factory.task import Task

CONFIG = RunnerConfig(model="sonnet", budget_usd=0.5)


class RaisingRunner:
    def run(self, task, config):
        raise FileNotFoundError("no such cwd: /w/target")


def test_a_runner_exception_is_logged_with_its_text_for_the_stage(tmp_path, caplog):
    ledger = Ledger(tmp_path / "ledger.jsonl")

    with caplog.at_level(logging.WARNING, logger="factory"):
        result = call_agent(Task(id="t1", prompt="p"), "prompt", RaisingRunner(), CONFIG, ledger, "plan")

    assert result == Infra("runner_error")
    assert any("t1" in r.getMessage() and "plan" in r.getMessage() and "no such cwd: /w/target" in r.getMessage() for r in caplog.records)
