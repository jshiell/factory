import hashlib
import json

import pytest

from gitutil import git

from factory.ledger import Ledger
from factory.plan_task import MAX_PLAN_BYTES, PLAN_CONFIG, PLAN_SCHEMA, build_plan_prompt, plan_task
from factory.command import RunnerConfig
from factory.step import BudgetCapReached, MissingBudget, RunResult
from factory.store import TaskStore
from factory.task import InvalidTransition, Task, TaskState


def plan_stdout(plan="1. do x", risks=("r",), cost=0.05):
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": cost,
                       "structured_output": {"plan": plan, "risks": list(risks)}})


class PlanRunner:
    def __init__(self, stdout="", timed_out=False, raises=None, on_run=None):
        self._result = RunResult(stdout, 0, timed_out)
        self._raises, self._on_run, self.tasks = raises, on_run, []

    def run(self, task, config):
        self.tasks.append(task)
        if self._on_run:
            self._on_run()
        if self._raises:
            raise self._raises
        return self._result


class Worktrees:
    def __init__(self, repo, fail=False):
        self.repo, self.fail, self.created = repo, fail, []

    def create(self, task_id):
        if self.fail:
            raise RuntimeError("git exploded")
        self.created.append(task_id)
        return self.repo, git(self.repo, "rev-parse", "HEAD")


@pytest.fixture
def store(tmp_path):
    (tmp_path / "tasks").mkdir()
    store = TaskStore(tmp_path / "tasks")
    store.save(Task(id="t1", prompt="", state=TaskState.PROPOSED, goal="add multiply"))
    return store


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "ledger.jsonl")


def plan(store, ledger, runner, worktrees, config=PLAN_CONFIG, cap=10):
    return plan_task("t1", store, ledger, runner, config, cap_usd=cap, worktrees=worktrees)


def test_the_plan_prompt_carries_the_goal_and_never_starts_with_a_dash():
    prompt = build_plan_prompt("add multiply")

    assert "add multiply" in prompt and not prompt.startswith("-")


def test_the_plan_config_is_read_only_with_a_plan_schema():
    assert PLAN_CONFIG.allowed_tools == ("Read", "Grep", "Glob")
    assert set(PLAN_CONFIG.disallowed_tools) == {"Write", "Edit", "NotebookEdit", "Bash"}
    assert PLAN_CONFIG.permission_mode is None
    assert PLAN_SCHEMA["required"] == ["plan", "risks"]
    assert PLAN_CONFIG.max_turns and 0 < PLAN_CONFIG.budget_usd < 0.5


def test_a_task_that_is_not_proposed_is_rejected_first(repo, store, ledger):
    store.save(Task(id="t1", prompt="p", state=TaskState.QUEUED))
    worktrees = Worktrees(repo)

    with pytest.raises(InvalidTransition):
        plan(store, ledger, PlanRunner(), worktrees, config=RunnerConfig(model="sonnet"))

    assert worktrees.created == []


def test_missing_budget_or_reached_cap_raises_and_creates_nothing(repo, store, ledger):
    worktrees, runner = Worktrees(repo), PlanRunner(plan_stdout())
    with pytest.raises(MissingBudget):
        plan(store, ledger, runner, worktrees, config=RunnerConfig(model="sonnet"))
    with pytest.raises(BudgetCapReached):
        plan(store, ledger, runner, worktrees, cap=0.01)

    assert worktrees.created == [] and runner.tasks == []


def test_worktree_is_persisted_before_the_planner_runs(repo, store, ledger):
    seen = []
    runner = PlanRunner(plan_stdout(), on_run=lambda: seen.append(store.load("t1")))

    plan(store, ledger, runner, Worktrees(repo))

    assert seen[0].worktree == str(repo) and seen[0].base_sha == git(repo, "rev-parse", "HEAD")


def test_worktree_creation_failure_leaves_the_task_proposed_and_the_planner_unrun(repo, store, ledger):
    runner = PlanRunner(plan_stdout())

    with pytest.raises(RuntimeError):
        plan(store, ledger, runner, Worktrees(repo, fail=True))

    assert store.load("t1").state is TaskState.PROPOSED and runner.tasks == []


def test_the_planner_gets_the_plan_prompt_but_the_stored_prompt_stays_empty(repo, store, ledger):
    runner = PlanRunner(plan_stdout())

    plan(store, ledger, runner, Worktrees(repo))

    assert "add multiply" in runner.tasks[0].prompt
    assert store.load("t1").prompt == ""


def test_a_valid_plan_moves_the_task_to_planned_with_digest_risks_and_cost(repo, store, ledger):
    task = plan(store, ledger, PlanRunner(plan_stdout("1. do x", ("risky",), cost=0.07)), Worktrees(repo))

    assert task.state is TaskState.PLANNED
    assert (task.plan, task.plan_risks) == ("1. do x", ("risky",))
    assert task.plan_digest == hashlib.sha256(b"1. do x").hexdigest()
    assert store.load("t1") == task
    assert [(e["cost_usd"], e["stage"]) for e in ledger.entries()] == [(0.07, "plan")]


def test_an_oversized_plan_stays_proposed_with_plan_too_large(repo, store, ledger):
    task = plan(store, ledger, PlanRunner(plan_stdout("x" * (MAX_PLAN_BYTES + 1))), Worktrees(repo))

    assert (task.state, task.plan_error, task.plan) == (TaskState.PROPOSED, "plan_too_large", None)


def bad_structured(output):
    return json.dumps({"type": "result", "subtype": "success", "is_error": False,
                       "total_cost_usd": 0.05, "structured_output": output})


BAD = {
    "empty_plan": bad_structured({"plan": "  ", "risks": []}),
    "non_string_plan": bad_structured({"plan": 3, "risks": []}),
    "bad_risks": bad_structured({"plan": "p", "risks": [1]}),
    "missing_output": json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.05}),
    "unparseable": "nope",
    "error": json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True, "total_cost_usd": 0.0}),
    "budget": json.dumps({"type": "result", "subtype": "error_max_budget_usd", "is_error": True, "total_cost_usd": 0.31}),
}


@pytest.mark.parametrize("name", BAD)
def test_bad_planner_output_leaves_the_task_proposed_with_the_reason(repo, store, ledger, name):
    task = plan(store, ledger, PlanRunner(BAD[name]), Worktrees(repo))

    assert task.state is TaskState.PROPOSED and task.plan_error
    assert store.load("t1") == task


def test_timeout_and_exception_leave_the_task_proposed_and_charge_the_assumed_cost(repo, store, ledger):
    for runner, reason in [(PlanRunner(timed_out=True), "timeout"), (PlanRunner(raises=OSError()), "runner_error")]:
        assert plan(store, ledger, runner, Worktrees(repo)).plan_error == reason

    assert ledger.total() == pytest.approx(2 * 2 * PLAN_CONFIG.budget_usd)


def test_retry_after_an_infrastructure_failure_reuses_the_worktree_and_clears_the_error(repo, store, ledger):
    worktrees = Worktrees(repo)
    plan(store, ledger, PlanRunner(timed_out=True), worktrees)

    task = plan(store, ledger, PlanRunner(plan_stdout()), worktrees)

    assert worktrees.created == ["t1"]
    assert (task.state, task.plan_error) == (TaskState.PLANNED, None)


def test_a_planner_that_modifies_the_worktree_fails_the_task(repo, store, ledger):
    runner = PlanRunner(plan_stdout(), on_run=lambda: (repo / "evil.txt").write_text("e"))

    task = plan(store, ledger, runner, Worktrees(repo))

    assert (task.state, task.failure_reason) == (TaskState.FAILED, "plan_modified_worktree")
    assert task.plan_error is None
