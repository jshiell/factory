from factory.audit_task import AUDIT_CONFIG, build_audit_prompt
from factory.task import Task


def test_audit_prompt_contains_the_task_prompt_and_the_diff_marked_untrusted():
    prompt = build_audit_prompt(Task(id="t1", prompt="add subtract"), "+def subtract(a, b): ...")

    assert "add subtract" in prompt
    assert "+def subtract(a, b): ..." in prompt
    assert "untrusted" in prompt.lower()
    assert not prompt.startswith("-")


def test_audit_config_is_read_only_with_a_verdict_schema_and_a_modest_budget():
    assert AUDIT_CONFIG.allowed_tools == ()
    assert AUDIT_CONFIG.confine is True
    assert set(AUDIT_CONFIG.disallowed_tools) == {"Write", "Edit", "NotebookEdit", "Bash", "Glob"}
    assert AUDIT_CONFIG.permission_mode is None
    assert AUDIT_CONFIG.json_schema["properties"]["verdict"]["enum"] == ["pass", "fail"]
    assert AUDIT_CONFIG.max_turns is not None
    assert 0 < AUDIT_CONFIG.budget_usd < 0.5


import json

import pytest

from gitutil import git

from factory.audit_task import MAX_DIFF_BYTES, audit_task
from factory.command import RunnerConfig
from factory.ledger import Ledger
from factory.step import BudgetCapReached, MissingBudget, RunResult
from factory.store import TaskStore
from factory.task import InvalidTransition, TaskState


def verdict_stdout(verdict="pass", findings=("ok",), cost=0.05, **overrides):
    payload = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": cost,
               "structured_output": {"verdict": verdict, "findings": list(findings)}, **overrides}
    return json.dumps(payload)


class AuditRunner:
    def __init__(self, stdout="", exit_code=0, timed_out=False, raises=None, on_run=None):
        self._result = RunResult(stdout, exit_code, timed_out)
        self._raises, self._on_run = raises, on_run
        self.tasks = []

    def run(self, task, config):
        self.tasks.append(task)
        if self._on_run:
            self._on_run()
        if self._raises:
            raise self._raises
        return self._result


@pytest.fixture
def store(tmp_path):
    (tmp_path / "tasks").mkdir()
    return TaskStore(tmp_path / "tasks")


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "ledger.jsonl")


@pytest.fixture
def verified(repo, store):
    (repo / "proj").mkdir()
    (repo / "proj" / "code.py").write_text("x")
    git(repo, "add", "proj")
    git(repo, "commit", "-q", "-m", "project")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "proj" / "code.py").write_text("y")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "change")
    store.save(Task(id="t1", prompt="make y", state=TaskState.VERIFIED, worktree=str(repo), base_sha=base))
    return repo


def audit(store, ledger, runner, config=AUDIT_CONFIG, cap=10):
    return audit_task("t1", store, ledger, runner, config, cap_usd=cap, project_subdir="proj")


def test_a_task_that_is_not_verified_is_rejected_before_budget_or_git(repo, store, ledger):
    store.save(Task(id="t1", prompt="p", state=TaskState.AGENT_DONE, worktree="/nonexistent"))
    runner = AuditRunner()

    with pytest.raises(InvalidTransition):
        audit(store, ledger, runner, config=RunnerConfig(model="sonnet"))

    assert runner.tasks == []


def test_missing_budget_or_reached_cap_raises_without_running(verified, store, ledger):
    runner = AuditRunner(verdict_stdout())
    with pytest.raises(MissingBudget):
        audit(store, ledger, runner, config=RunnerConfig(model="sonnet"))
    with pytest.raises(BudgetCapReached):
        audit(store, ledger, runner, cap=0.01)

    assert runner.tasks == []
    assert store.load("t1").state is TaskState.VERIFIED


def test_an_oversized_diff_fails_the_task_without_cost(verified, store, ledger):
    (verified / "proj" / "code.py").write_text("z" * (MAX_DIFF_BYTES + 1))
    git(verified, "add", "-A")
    git(verified, "commit", "-q", "-m", "big")
    runner = AuditRunner(verdict_stdout())

    task = audit(store, ledger, runner)

    assert (task.state, task.failure_reason) == (TaskState.FAILED, "diff_too_large")
    assert runner.tasks == [] and ledger.total() == 0


def test_the_runner_gets_the_audit_prompt_but_the_stored_task_keeps_its_own(verified, store, ledger):
    runner = AuditRunner(verdict_stdout())

    audit(store, ledger, runner)

    assert "make y" in runner.tasks[0].prompt and "+y" in runner.tasks[0].prompt
    assert store.load("t1").prompt == "make y"


def test_a_pass_verdict_audits_the_task_and_records_cost_findings_and_sha(verified, store, ledger):
    task = audit(store, ledger, AuditRunner(verdict_stdout("pass", ("fine",), cost=0.07)))

    assert task.state is TaskState.AUDITED
    assert task.audit_findings == ("fine",)
    assert task.audited_sha == git(verified, "rev-parse", "HEAD")
    assert store.load("t1") == task
    assert ledger.total() == pytest.approx(0.07)


def test_a_fail_verdict_fails_the_task_as_audit_rejected_with_findings(verified, store, ledger):
    task = audit(store, ledger, AuditRunner(verdict_stdout("fail", ("skips tests",))))

    assert (task.state, task.failure_reason) == (TaskState.FAILED, "audit_rejected")
    assert task.audit_findings == ("skips tests",)


BAD_OUTPUTS = {
    "malformed": json.dumps({"type": "result", "subtype": "success", "is_error": False,
                             "total_cost_usd": 0.05, "structured_output": {"verdict": "maybe", "findings": []}}),
    "missing": json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.05}),
    "unparseable": "not json",
    "budget": json.dumps({"type": "result", "subtype": "error_max_budget_usd", "is_error": True,
                          "total_cost_usd": 0.31}),
    "error": json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True,
                         "total_cost_usd": 0.01}),
}


@pytest.mark.parametrize("name", BAD_OUTPUTS)
def test_auditor_infrastructure_failure_leaves_the_task_verified_with_the_reason(verified, store, ledger, name):
    task = audit(store, ledger, AuditRunner(BAD_OUTPUTS[name]))

    assert task.state is TaskState.VERIFIED
    assert task.audit_error
    assert store.load("t1") == task


def test_timeout_and_runner_exception_leave_the_task_verified_and_charge_the_assumed_cost(
    verified, store, ledger
):
    for runner, reason in [(AuditRunner(timed_out=True), "timeout"), (AuditRunner(raises=OSError()), "runner_error")]:
        task = audit(store, ledger, runner)
        assert (task.state, task.audit_error) == (TaskState.VERIFIED, reason)

    assert ledger.total() == pytest.approx(2 * 2 * AUDIT_CONFIG.budget_usd)


def test_a_successful_retry_clears_the_earlier_audit_error(verified, store, ledger):
    audit(store, ledger, AuditRunner(timed_out=True))

    task = audit(store, ledger, AuditRunner(verdict_stdout()))

    assert (task.state, task.audit_error) == (TaskState.AUDITED, None)


def test_a_dirty_worktree_before_the_audit_fails_without_running(verified, store, ledger):
    (verified / "proj" / "stray.txt").write_text("s")
    runner = AuditRunner(verdict_stdout())

    task = audit(store, ledger, runner)

    assert task.failure_reason == "audit_modified_worktree"
    assert runner.tasks == []


@pytest.mark.parametrize("mutation", ["dirty", "commit"])
def test_an_auditor_that_changes_the_worktree_fails_the_task(verified, store, ledger, mutation):
    def tamper():
        (verified / "proj" / "evil.txt").write_text("e")
        if mutation == "commit":
            git(verified, "add", "-A")
            git(verified, "commit", "-q", "-m", "evil")

    task = audit(store, ledger, AuditRunner(verdict_stdout(), on_run=tamper))

    assert (task.state, task.failure_reason) == (TaskState.FAILED, "audit_modified_worktree")
    assert ledger.total() == pytest.approx(0.05)


def test_terminal_audit_failures_clear_an_earlier_audit_error(verified, store, ledger):
    audit(store, ledger, AuditRunner(timed_out=True))
    assert store.load("t1").audit_error == "timeout"

    task = audit(store, ledger, AuditRunner(verdict_stdout("fail", ("no",))))

    assert (task.failure_reason, task.audit_error) == ("audit_rejected", None)


def test_modified_worktree_clears_an_earlier_audit_error(verified, store, ledger):
    audit(store, ledger, AuditRunner(timed_out=True))
    (verified / "proj" / "stray.txt").write_text("s")

    task = audit(store, ledger, AuditRunner(verdict_stdout()))

    assert (task.failure_reason, task.audit_error) == ("audit_modified_worktree", None)


def test_diff_too_large_clears_an_earlier_audit_error(verified, store, ledger):
    audit(store, ledger, AuditRunner(timed_out=True))
    (verified / "proj" / "code.py").write_text("z" * (MAX_DIFF_BYTES + 1))
    git(verified, "add", "-A")
    git(verified, "commit", "-q", "-m", "big")

    task = audit(store, ledger, AuditRunner(verdict_stdout()))

    assert (task.failure_reason, task.audit_error) == ("diff_too_large", None)


def test_audit_costs_are_recorded_under_the_audit_stage(verified, store, ledger):
    audit(store, ledger, AuditRunner(timed_out=True))
    audit(store, ledger, AuditRunner(verdict_stdout()))

    assert [e["stage"] for e in ledger.entries()] == ["audit", "audit"]
