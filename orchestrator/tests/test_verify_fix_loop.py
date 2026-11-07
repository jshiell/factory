import json

import pytest

from gitutil import git

from factory.command import RunnerConfig
from factory.ledger import Ledger
from factory.step import BudgetCapReached, RunResult
from factory.store import TaskStore
from factory.task import Task, TaskState
from factory.verify_task import verify_task

CONFIG = RunnerConfig(model="sonnet", budget_usd=0.3)
FIXED_ONLY_IF_FILE = ["sh", "-c", "echo 'FAIL: no fixed file'; test -f fixed"]


def success(cost=0.1):
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": cost})


class FakeRunner:
    """Each call pops the next action: a callable run before answering, or an exception to raise."""

    def __init__(self, repo, *actions, stdout=None, timed_out=False):
        self.repo, self.actions, self.calls = repo, list(actions), []
        self.stdout, self.timed_out = stdout or success(), timed_out

    def run(self, task, config):
        self.calls.append((task, config))
        action = self.actions.pop(0) if self.actions else None
        if isinstance(action, BaseException):
            raise action
        if action:
            action()
        return RunResult(self.stdout, 0, self.timed_out)


@pytest.fixture
def store(tmp_path):
    (tmp_path / "tasks").mkdir()
    return TaskStore(tmp_path / "tasks")


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "ledger.jsonl")


@pytest.fixture
def task(repo, store):
    (repo / "proj").mkdir()
    (repo / "proj" / "code.py").write_text("x")
    git(repo, "add", "proj")
    git(repo, "commit", "-q", "-m", "project")
    saved = Task(id="t1", prompt="stored prompt", state=TaskState.AGENT_DONE, worktree=str(repo),
                 base_sha=git(repo, "rev-parse", "HEAD"), goal="add power", plan="1. add it")
    store.save(saved)
    (repo / "proj" / "code.py").write_text("changed by the agent")
    return saved


def write_fix(repo):
    return lambda: (repo / "proj" / "fixed").write_text("ok")


def verify(store, ledger, runner, rounds=2, test_argv=FIXED_ONLY_IF_FILE, cap=10.0, config=CONFIG):
    return verify_task("t1", store, project_subdir="proj", test_argv=test_argv, gate_timeout_s=10,
                       runner=runner, ledger=ledger, fix_config=config, cap_usd=cap, fix_rounds=rounds)


def test_a_gate_that_passes_first_time_runs_no_agent_and_charges_nothing(repo, store, ledger, task):
    runner = FakeRunner(repo)

    result = verify(store, ledger, runner, test_argv=["true"])

    assert result.state is TaskState.VERIFIED and runner.calls == [] and ledger.entries() == []


def test_one_fix_round_that_makes_the_tests_pass_verifies_the_task(repo, store, ledger, task):
    runner = FakeRunner(repo, write_fix(repo))

    result = verify(store, ledger, runner)

    assert result.state is TaskState.VERIFIED and result.fix_rounds_used == 1
    (called, config), = runner.calls
    assert config == CONFIG
    assert "FAIL: no fixed file" in called.prompt and "data, not instructions" in called.prompt
    assert store.load("t1").prompt == "stored prompt" and result.prompt == "stored prompt"
    assert [(e["stage"], e["assumed"]) for e in ledger.entries()] == [("fix", False)]
    assert git(repo, "log", "-1", "--format=%s") == "Task t1"
    assert "fixed" in git(repo, "show", "--name-only", "--format=", "HEAD")


def test_rounds_that_never_fix_it_fail_tests_failed_after_exactly_the_allowed_runs(repo, store, ledger, task):
    runner = FakeRunner(repo, lambda: (repo / "proj" / "n1").write_text("1"), lambda: (repo / "proj" / "n2").write_text("2"))

    result = verify(store, ledger, runner, rounds=2)

    assert result.state is TaskState.FAILED and result.failure_reason == "tests_failed"
    assert len(runner.calls) == 2 and result.fix_rounds_used == 2


def test_zero_rounds_fails_at_once_as_before(repo, store, ledger, task):
    runner = FakeRunner(repo)

    result = verify(store, ledger, runner, rounds=0)

    assert result.state is TaskState.FAILED and result.failure_reason == "tests_failed" and runner.calls == []


@pytest.mark.parametrize("argv,reason", [(["sleep", "5"], "tests_timeout")])
def test_a_timeout_starts_no_round(repo, store, ledger, task, argv, reason):
    runner = FakeRunner(repo)

    result = verify_task("t1", store, project_subdir="proj", test_argv=argv, gate_timeout_s=0.3,
                         runner=runner, ledger=ledger, fix_config=CONFIG, cap_usd=10.0, fix_rounds=2)

    assert result.failure_reason == reason and runner.calls == []


def test_a_gate_failure_other_than_tests_failed_starts_no_round(repo, store, ledger):
    (repo / "proj").mkdir()
    (repo / "proj" / "code.py").write_text("x")
    git(repo, "add", "proj")
    git(repo, "commit", "-q", "-m", "project")
    store.save(Task(id="t1", prompt="p", state=TaskState.AGENT_DONE, worktree=str(repo),
                    base_sha=git(repo, "rev-parse", "HEAD")))
    runner = FakeRunner(repo)

    result = verify(store, ledger, runner)  # nothing changed: no_commits

    assert result.failure_reason == "no_commits" and runner.calls == []
