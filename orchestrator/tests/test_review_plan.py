import json

import pytest

from factory.command import RunnerConfig
from factory.digest import plan_digest
from factory.ledger import Ledger
from factory.review_plan import REVIEW_CONFIG, REVIEW_SCHEMA, build_review_prompt, review_plan
from factory.step import BudgetCapReached, MissingBudget, RunResult
from factory.store import TaskStore
from factory.task import InvalidTransition, Task, TaskState


def verdict_stdout(verdict="approve", findings=("fine",), cost=0.04):
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": cost,
                       "structured_output": {"verdict": verdict, "findings": list(findings)}})


class ReviewRunner:
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


@pytest.fixture
def store(tmp_path, repo):
    (tmp_path / "tasks").mkdir()
    store = TaskStore(tmp_path / "tasks")
    store.save(Task(id="t1", prompt="", state=TaskState.PLANNED, goal="add multiply", plan="1. do x",
                    plan_digest=plan_digest("1. do x"), worktree=str(repo)))
    return store


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "ledger.jsonl")


def review(store, ledger, runner, config=REVIEW_CONFIG, cap=10):
    return review_plan("t1", store, ledger, runner, config, cap_usd=cap)


def test_the_review_prompt_has_goal_and_plan_with_the_plan_marked_untrusted():
    prompt = build_review_prompt("add multiply", "1. do x")

    assert "add multiply" in prompt and "1. do x" in prompt
    assert "untrusted" in prompt.lower() and not prompt.startswith("-")


def test_the_review_config_is_read_only_with_an_approve_or_revise_schema():
    assert REVIEW_CONFIG.allowed_tools == ("Read", "Grep", "Glob")
    assert set(REVIEW_CONFIG.disallowed_tools) == {"Write", "Edit", "NotebookEdit", "Bash"}
    assert REVIEW_CONFIG.permission_mode is None
    assert REVIEW_SCHEMA["properties"]["verdict"]["enum"] == ["approve", "revise"]
    assert REVIEW_CONFIG.max_turns and 0 < REVIEW_CONFIG.budget_usd < 0.5


def test_a_task_that_is_not_planned_is_rejected_first(store, ledger):
    store.save(Task(id="t1", prompt="", state=TaskState.PROPOSED))
    runner = ReviewRunner()

    with pytest.raises(InvalidTransition):
        review(store, ledger, runner, config=RunnerConfig(model="sonnet"))

    assert runner.tasks == []


def test_a_plan_that_no_longer_matches_its_digest_fails_before_budget_and_cost(store, ledger):
    store.save(Task(**{**store.load("t1").__dict__, "plan": "1. do something else"}))
    runner = ReviewRunner(verdict_stdout())

    task = review(store, ledger, runner, config=RunnerConfig(model="sonnet"))

    assert (task.state, task.failure_reason) == (TaskState.FAILED, "plan_changed")
    assert runner.tasks == [] and ledger.total() == 0


def test_missing_budget_or_reached_cap_raises_without_running(store, ledger):
    runner = ReviewRunner(verdict_stdout())
    with pytest.raises(MissingBudget):
        review(store, ledger, runner, config=RunnerConfig(model="sonnet"))
    with pytest.raises(BudgetCapReached):
        review(store, ledger, runner, cap=0.01)

    assert runner.tasks == [] and store.load("t1").state is TaskState.PLANNED


def test_the_reviewer_gets_the_review_prompt_and_the_stored_prompt_stays_empty(store, ledger):
    runner = ReviewRunner(verdict_stdout())

    review(store, ledger, runner)

    assert "1. do x" in runner.tasks[0].prompt and store.load("t1").prompt == ""


def test_approve_moves_the_task_to_reviewed_with_findings_and_cost(store, ledger):
    task = review(store, ledger, ReviewRunner(verdict_stdout("approve", ("good",), cost=0.06)))

    assert (task.state, task.review_findings) == (TaskState.REVIEWED, ("good",))
    assert store.load("t1") == task
    assert [(e["cost_usd"], e["stage"]) for e in ledger.entries()] == [(0.06, "review")]


def test_revise_fails_the_task_as_plan_rejected_with_findings(store, ledger):
    task = review(store, ledger, ReviewRunner(verdict_stdout("revise", ("deletes a test",))))

    assert (task.state, task.failure_reason) == (TaskState.FAILED, "plan_rejected")
    assert task.review_findings == ("deletes a test",)


BAD = {
    "unknown_verdict": json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.04,
                                   "structured_output": {"verdict": "pass", "findings": []}}),
    "bad_findings": json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.04,
                                "structured_output": {"verdict": "approve", "findings": [3]}}),
    "missing": json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.04}),
    "unparseable": "nope",
    "budget": json.dumps({"type": "result", "subtype": "error_max_budget_usd", "is_error": True, "total_cost_usd": 0.31}),
}


@pytest.mark.parametrize("name", BAD)
def test_reviewer_infrastructure_failure_leaves_the_task_planned_with_the_reason(store, ledger, name):
    task = review(store, ledger, ReviewRunner(BAD[name]))

    assert task.state is TaskState.PLANNED and task.review_error
    assert store.load("t1") == task


def test_timeout_and_exception_leave_the_task_planned_and_charge_the_assumed_cost(store, ledger):
    for runner, reason in [(ReviewRunner(timed_out=True), "timeout"), (ReviewRunner(raises=OSError()), "runner_error")]:
        assert review(store, ledger, runner).review_error == reason

    assert ledger.total() == pytest.approx(2 * 2 * REVIEW_CONFIG.budget_usd)


def test_a_verdict_after_an_infrastructure_failure_clears_the_review_error(store, ledger):
    review(store, ledger, ReviewRunner(timed_out=True))
    assert store.load("t1").review_error == "timeout"

    assert review(store, ledger, ReviewRunner(verdict_stdout())).review_error is None


def test_a_revise_verdict_after_an_infrastructure_failure_clears_the_review_error(store, ledger):
    review(store, ledger, ReviewRunner(timed_out=True))

    assert review(store, ledger, ReviewRunner(verdict_stdout("revise"))).review_error is None


def test_a_reviewer_that_modifies_the_worktree_fails_the_task(repo, store, ledger):
    runner = ReviewRunner(verdict_stdout(), on_run=lambda: (repo / "evil.txt").write_text("e"))

    task = review(store, ledger, runner)

    assert (task.state, task.failure_reason) == (TaskState.FAILED, "review_modified_worktree")
