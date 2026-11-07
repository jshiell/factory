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


def test_a_round_the_cap_cannot_afford_raises_leaving_the_task_untouched(repo, store, ledger, task):
    ledger.append("other", 9.9)
    runner = FakeRunner(repo)

    with pytest.raises(BudgetCapReached):
        verify(store, ledger, runner, cap=10.0)

    saved = store.load("t1")
    assert saved.state is TaskState.AGENT_DONE and saved.fix_rounds_used == 0 and runner.calls == []


def test_a_runner_exception_charges_the_assumed_cost_and_fails_fix_runner_error(repo, store, ledger, task):
    result = verify(store, ledger, FakeRunner(repo, RuntimeError("boom")))

    assert (result.state, result.failure_reason) == (TaskState.FAILED, "fix_runner_error")
    assert [(e["stage"], e["assumed"], e["cost_usd"]) for e in ledger.entries()] == [("fix", True, 0.6)]


def test_a_runner_timeout_fails_fix_timeout(repo, store, ledger, task):
    result = verify(store, ledger, FakeRunner(repo, timed_out=True))

    assert result.failure_reason == "fix_timeout"
    assert ledger.entries()[0]["assumed"] is True


def test_a_non_success_agent_outcome_fails_with_its_kind(repo, store, ledger, task):
    errored = json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True})

    result = verify(store, ledger, FakeRunner(repo, stdout=errored))

    assert result.failure_reason == "fix_error"


def test_an_agent_that_moves_head_fails_fix_moved(repo, store, ledger, task):
    def commit():
        (repo / "proj" / "fixed").write_text("ok")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "agent commit")

    result = verify(store, ledger, FakeRunner(repo, commit))

    assert result.failure_reason == "fix_moved"


def test_a_nested_repo_created_in_a_round_fails_before_anything_is_committed(repo, store, ledger, task):
    def plant():
        (repo / "proj" / "x" / ".git").mkdir(parents=True)
        (repo / "proj" / "x" / "file").write_text("f")

    runner = FakeRunner(repo, plant)
    verify(store, ledger, FakeRunner(repo), rounds=0)  # first pass: committed, tests fail
    store.save(task)
    head = git(repo, "rev-parse", "HEAD")

    result = verify(store, ledger, runner)

    assert result.failure_reason == "nested_repo"
    assert git(repo, "rev-parse", "HEAD") == head


def test_a_round_that_deletes_a_line_of_an_existing_test_fails_tests_modified(repo, store, ledger):
    (repo / "proj").mkdir()
    (repo / "proj" / "test_old.py").write_text("def test_a():\n    pass\n\n\ndef test_b():\n    pass\n")
    git(repo, "add", "proj")
    git(repo, "commit", "-q", "-m", "project")
    store.save(Task(id="t1", prompt="p", state=TaskState.AGENT_DONE, worktree=str(repo),
                    base_sha=git(repo, "rev-parse", "HEAD")))
    (repo / "proj" / "test_old.py").write_text("def test_a():\n    pass\n\n\ndef test_b():\n    pass\n\n\ndef test_c():\n    pass\n")
    weaken = lambda: (repo / "proj" / "test_old.py").write_text("def test_a():\n    pass\n")

    result = verify(store, ledger, FakeRunner(repo, weaken))

    assert result.failure_reason == "tests_modified"


def test_a_round_that_changes_nothing_fails_fix_no_changes_without_burning_more_rounds(repo, store, ledger, task):
    runner = FakeRunner(repo)

    result = verify(store, ledger, runner, rounds=2)

    assert result.failure_reason == "fix_no_changes" and len(runner.calls) == 1


def test_the_counter_and_marker_are_saved_before_the_agent_runs(repo, store, ledger, task):
    seen = []
    runner = FakeRunner(repo, lambda: seen.append(store.load("t1")), write_fix(repo))

    result = verify(store, ledger, runner)

    assert (seen[0].fix_rounds_used, seen[0].fix_in_flight) == (1, True)
    assert result.fix_in_flight is False and store.load("t1").fix_in_flight is False


def test_a_resumed_task_only_gets_the_remaining_rounds(repo, store, ledger, task):
    store.save(Task(**{**task.__dict__, "fix_rounds_used": 1}))
    runner = FakeRunner(repo, lambda: (repo / "proj" / "n").write_text("1"), lambda: (repo / "proj" / "m").write_text("2"))

    result = verify(store, ledger, runner, rounds=2)

    assert len(runner.calls) == 1 and result.failure_reason == "tests_failed" and result.fix_rounds_used == 2


def test_a_task_left_with_the_marker_set_fails_fix_interrupted_touching_no_git(repo, store, ledger, task):
    store.save(Task(**{**task.__dict__, "fix_rounds_used": 1, "fix_in_flight": True}))
    head = git(repo, "rev-parse", "HEAD")
    runner = FakeRunner(repo)

    result = verify(store, ledger, runner)

    assert (result.state, result.failure_reason, result.fix_in_flight) == (TaskState.FAILED, "fix_interrupted", False)
    assert runner.calls == [] and git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "status", "--porcelain") != ""  # the agent's possibly-live edits stay uncommitted
    assert [(e["stage"], e["assumed"], e["cost_usd"]) for e in ledger.entries()] == [("fix", True, 0.6)]


def test_the_ledger_and_state_are_written_before_any_later_git_call_can_fail(repo, store, ledger, task):
    import shutil
    runner = FakeRunner(repo, lambda: shutil.rmtree(repo / ".git"))

    with pytest.raises(Exception):
        verify(store, ledger, runner)

    assert [e["stage"] for e in ledger.entries()] == ["fix"]
    assert store.load("t1").fix_in_flight is False


def test_the_pre_round_head_is_saved_on_the_task_before_the_agent_runs(repo, store, ledger, task):
    seen = []
    runner = FakeRunner(repo, lambda: seen.append(store.load("t1")), write_fix(repo))

    verify(store, ledger, runner)

    # the implementer's work is committed by the first gate run, so the pre-round HEAD is that commit
    assert seen[0].fix_frozen_sha == git(repo, "rev-list", "--max-count=1", "--grep=Task t1", "HEAD")
