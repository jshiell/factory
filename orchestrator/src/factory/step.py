import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Protocol

from factory import failure_codes
from factory.classify import Outcome, OutcomeKind, classify
from factory.command import PromptTooLarge, RunnerConfig
from factory.evidence import bounded_evidence
from factory.ledger import Ledger
from factory.store import TaskStore
from factory.task import Event, Task, apply

ASSUMED_COST_MULTIPLIER = 2


# Fixed sentences: the exception text can hold paths and command lines that must not be stored or shown.
RUNNER_ERROR_EVIDENCE = "The agent could not be started or crashed before it produced a result."
PROMPT_TOO_LARGE_EVIDENCE = "The prompt was too large to pass to the agent."


class BudgetCapReached(Exception):
    pass


class MissingBudget(Exception):
    pass


TAIL_CHARS = 2000  # how much of the agent's stderr is kept, and of each stream when an unparseable run is logged


@dataclass(frozen=True)
class RunResult:
    stdout: str
    exit_code: int
    timed_out: bool = False
    stderr: str = ""
    parse: Callable[[str, int], Outcome] = field(default=classify, repr=False, compare=False)


class Runner(Protocol):
    def run(self, task: Task, config: RunnerConfig) -> RunResult: ...


def classify_run(result: RunResult, task_id: str, stage: str) -> Outcome:
    """Classify a finished run; an unparseable one is logged with the tails of its output.
    `%r` keeps agent output from forging log lines."""
    outcome = result.parse(result.stdout, result.exit_code)
    if outcome.kind is OutcomeKind.UNPARSEABLE:
        logging.getLogger("factory").warning(
            "agent result for task %s, stage %s is unparseable: exit code %s, stdout tail %r, stderr tail %r",
            task_id, stage, result.exit_code, result.stdout[-TAIL_CHARS:], result.stderr[-TAIL_CHARS:],
        )
    return outcome


def preflight(
    task_id: str, store: TaskStore, ledger: Ledger, config: RunnerConfig, cap_usd: float
) -> Task:
    """Return the task as it would be once running; raise if it must not run."""
    running = apply(store.load(task_id), Event.START)
    check_budget(task_id, ledger, config, cap_usd)
    return running


def check_budget(task_id: str, ledger: Ledger, config: RunnerConfig, cap_usd: float) -> None:
    if config.budget_usd is None:
        raise MissingBudget(task_id)
    if ledger.would_exceed(cap_usd, config.budget_usd):
        raise BudgetCapReached(task_id)


def step(
    task_id: str,
    store: TaskStore,
    ledger: Ledger,
    runner: Runner,
    config: RunnerConfig,
    cap_usd: float,
) -> Task:
    running = preflight(task_id, store, ledger, config, cap_usd)
    store.save(running)
    try:
        result = runner.run(running, config)
    except PromptTooLarge as error:
        logging.getLogger("factory").warning("agent run refused for task %s, stage implement: %s", task_id, error)
        failed = replace(
            apply(running, Event.FAILED, reason=failure_codes.PROMPT_TOO_LARGE),
            failure_evidence=PROMPT_TOO_LARGE_EVIDENCE,
        )
        store.save(failed)
        return failed
    except Exception as error:
        logging.getLogger("factory").warning("agent run failed for task %s, stage implement: %r", task_id, error)
        return _fail_with_assumed_cost(
            running, failure_codes.RUNNER_ERROR, store, ledger, config, RUNNER_ERROR_EVIDENCE
        )
    if result.timed_out:
        evidence = bounded_evidence([("stderr", result.stderr)])
        return _fail_with_assumed_cost(running, failure_codes.TIMEOUT, store, ledger, config, evidence)
    outcome = classify_run(result, task_id, "implement")
    ledger.append(task_id, outcome.cost_usd, stage="implement")
    if outcome.kind in (OutcomeKind.SUCCESS, OutcomeKind.SUCCESS_WITH_DENIALS):
        finished = replace(apply(running, Event.SUCCEEDED), denials=outcome.denials)
    else:
        failed = apply(running, Event.FAILED, reason=outcome.kind.value)
        finished = replace(failed, failure_evidence=outcome_evidence(outcome, result.stderr))
    store.save(finished)
    return finished


def outcome_evidence(outcome: Outcome, stderr: str) -> str:
    return bounded_evidence([
        ("subtype", outcome.subtype or ""),
        ("result", outcome.result or ""),
        ("errors", "\n".join(outcome.errors)),
        ("stderr", stderr),
    ])


def _fail_with_assumed_cost(
    running: Task, reason: str, store: TaskStore, ledger: Ledger, config: RunnerConfig, evidence: str = ""
) -> Task:
    ledger.append(running.id, ASSUMED_COST_MULTIPLIER * config.budget_usd, assumed=True, stage="implement")
    failed = replace(apply(running, Event.FAILED, reason=reason), failure_evidence=evidence)
    store.save(failed)
    return failed
