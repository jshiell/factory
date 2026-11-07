import json

import pytest

from gitutil import git

from factory.command import RunnerConfig
from factory.ledger import Ledger
from factory.readonly_stage import Infra, Modified, Ran, run_readonly_stage
from factory.step import RunResult
from factory.task import Task

CONFIG = RunnerConfig(model="sonnet", budget_usd=0.3)


def stdout(output=None, cost=0.05, subtype="success", is_error=False):
    payload = {"type": "result", "subtype": subtype, "is_error": is_error, "total_cost_usd": cost}
    if output is not None:
        payload["structured_output"] = output
    return json.dumps(payload)


class Runner:
    def __init__(self, result=None, raises=None, on_run=None):
        self.result = result or RunResult(stdout({"ok": 1}), 0)
        self.raises, self.on_run, self.tasks = raises, on_run, []

    def run(self, task, config):
        self.tasks.append(task)
        if self.on_run:
            self.on_run()
        if self.raises:
            raise self.raises
        return self.result


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "ledger.jsonl")


@pytest.fixture
def task(repo):
    return Task(id="t1", prompt="stored", worktree=str(repo))


def stage(task, runner, ledger, name="audit"):
    return run_readonly_stage(task, "STAGE PROMPT", runner, CONFIG, ledger, name)


def test_a_clean_run_returns_the_output_and_head_and_charges_the_stage(repo, task, ledger):
    runner = Runner()

    result = stage(task, runner, ledger, "review")

    assert result == Ran({"ok": 1}, git(repo, "rev-parse", "HEAD"))
    assert runner.tasks[0].prompt == "STAGE PROMPT"
    assert [(e["cost_usd"], e["stage"]) for e in ledger.entries()] == [(0.05, "review")]


def test_a_dirty_worktree_before_the_run_is_modified_and_the_runner_is_not_called(repo, task, ledger):
    (repo / "stray.txt").write_text("s")
    runner = Runner()

    assert stage(task, runner, ledger) == Modified()
    assert runner.tasks == [] and ledger.total() == 0


@pytest.mark.parametrize("commit", [False, True])
def test_a_worktree_changed_by_the_run_is_modified_after_charging_the_cost(repo, task, ledger, commit):
    def tamper():
        (repo / "evil.txt").write_text("e")
        if commit:
            git(repo, "add", "-A")
            git(repo, "commit", "-q", "-m", "evil")

    assert stage(task, Runner(on_run=tamper), ledger) == Modified()
    assert ledger.total() == pytest.approx(0.05)


@pytest.mark.parametrize(
    "runner,reason",
    [
        (Runner(RunResult("", 0, timed_out=True)), "timeout"),
        (Runner(raises=OSError()), "runner_error"),
    ],
)
def test_timeout_and_exception_are_infra_failures_charged_at_the_assumed_cost(repo, task, ledger, runner, reason):
    assert stage(task, runner, ledger) == Infra(reason)

    entry = ledger.entries()[0]
    assert (entry["cost_usd"], entry["assumed"], entry["stage"]) == (0.6, True, "audit")


def test_a_timed_out_run_that_dirtied_the_tree_is_reported_as_a_timeout(repo, task, ledger):
    runner = Runner(RunResult("", 0, timed_out=True), on_run=lambda: (repo / "x.txt").write_text("x"))

    assert stage(task, runner, ledger) == Infra("timeout")


@pytest.mark.parametrize(
    "raw,reason",
    [
        ("not json", "unparseable"),
        (stdout(subtype="error_max_budget_usd", is_error=True, cost=0.31), "budget_exhausted"),
        (stdout(subtype="error_during_execution", is_error=True), "error"),
    ],
)
def test_non_success_outcomes_are_infra_failures_with_the_cost_charged(repo, task, ledger, raw, reason):
    assert stage(task, Runner(RunResult(raw, 0)), ledger) == Infra(reason)


def test_a_success_without_structured_output_returns_none_output(repo, task, ledger):
    assert stage(task, Runner(RunResult(stdout(), 0)), ledger) == Ran(None, git(repo, "rev-parse", "HEAD"))
