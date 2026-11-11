import logging
from dataclasses import dataclass, replace

from factory.classify import Outcome, classify
from factory.command import RunnerConfig
from factory.ledger import Ledger
from factory.step import ASSUMED_COST_MULTIPLIER, Runner
from factory.task import Task


@dataclass(frozen=True)
class Infra:
    reason: str


@dataclass(frozen=True)
class Completed:
    outcome: Outcome


def call_agent(
    task: Task, prompt: str, runner: Runner, config: RunnerConfig, ledger: Ledger, stage: str
) -> Infra | Completed:
    """Run the agent with `prompt` (never saved on the task); every outcome is charged to the ledger."""
    try:
        result = runner.run(replace(task, prompt=prompt), config)
    except Exception as error:
        logging.getLogger("factory").warning("agent run failed for task %s, stage %s: %r", task.id, stage, error)
        return _assumed_failure("runner_error", task, config, ledger, stage)
    if result.timed_out:
        return _assumed_failure("timeout", task, config, ledger, stage)
    outcome = classify(result.stdout, result.exit_code)
    ledger.append(task.id, outcome.cost_usd, stage=stage)
    return Completed(outcome)


def _assumed_failure(reason: str, task: Task, config: RunnerConfig, ledger: Ledger, stage: str) -> Infra:
    ledger.append(task.id, ASSUMED_COST_MULTIPLIER * config.budget_usd, assumed=True, stage=stage)
    return Infra(reason)
