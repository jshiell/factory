import pytest

from gitutil import git

from factory.audit_corpus import BASE_FILES, CASES, PROJECT_SUBDIR
from factory.audit_eval import build_base_repo, materialise
from factory.worktrees import WorktreeManager


@pytest.fixture(autouse=True)
def no_global_git_config(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")


def test_the_base_repo_holds_the_inlined_project_under_the_project_subdir_in_one_commit(tmp_path):
    repo = build_base_repo(tmp_path)

    for rel, content in BASE_FILES.items():
        assert (repo / PROJECT_SUBDIR / rel).read_text() == content
    assert git(repo, "rev-list", "--count", "HEAD") == "1"


def test_materialise_commits_the_after_state_on_a_fresh_worktree_with_neutral_names(tmp_path):
    case = next(c for c in CASES if c.id == "case-01")
    manager = WorktreeManager(build_base_repo(tmp_path), tmp_path / "wt")

    worktree, base_sha = materialise(case, manager, "a1-case-01")

    assert git(worktree, "rev-parse", "HEAD") != base_sha and git(worktree, "status", "--porcelain") == ""
    assert "def subtract" in (worktree / PROJECT_SUBDIR / "src/calc/__init__.py").read_text()
    assert git(worktree, "log", "-1", "--format=%s|%an") == "change|factory"
    assert case.label not in git(worktree, "log", "--format=%s%b%an") and case.category not in git(worktree, "branch", "--list")


# --- one audited run ------------------------------------------------------------

import json
import os
from pathlib import Path

from factory.audit_eval import run_one
from factory.ledger import Ledger
from factory.step import RunResult
from factory.store import TaskStore


def verdict_stdout(verdict, findings=("because",), cost=0.05):
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": cost,
                       "structured_output": {"verdict": verdict, "findings": list(findings)}})


class CannedRunner:
    def __init__(self, result, before=None):
        self.result, self.before, self.tasks = result, before, []

    def run(self, task, config):
        self.tasks.append(task)
        if self.before:
            self.before(Path(task.worktree))
        return self.result


@pytest.fixture
def rig(tmp_path):
    (tmp_path / "eval" / "tasks").mkdir(parents=True)
    manager = WorktreeManager(build_base_repo(tmp_path), tmp_path / "wt")
    return manager, TaskStore(tmp_path / "eval" / "tasks"), Ledger(tmp_path / "eval" / "ledger.jsonl"), tmp_path / "eval"


def audit(rig, runner, case_id="case-01", run=1, cap=5.0):
    manager, store, ledger, eval_dir = rig
    case = next(c for c in CASES if c.id == case_id)
    return run_one(case, run, manager, store, ledger, runner, cap, eval_dir)


def test_a_passing_verdict_is_recorded_with_its_cost_and_the_worktree_is_removed(rig, tmp_path):
    record = audit(rig, CannedRunner(RunResult(verdict_stdout("pass", findings=(), cost=0.07), 0)))

    assert (record["case"], record["run"], record["label"], record["outcome"]) == ("case-01", 1, "pass", "pass")
    assert record["cost_usd"] == 0.07 and record["assumed_usd"] == 0
    assert json.loads((rig[3] / "case-01-r1.json").read_text()) == record
    assert not (tmp_path / "wt" / "a1-case-01").exists()


def test_the_auditor_is_given_the_production_task_prompt_inside_the_audit_prompt(rig):
    runner = CannedRunner(RunResult(verdict_stdout("pass", findings=()), 0))

    audit(rig, runner)

    (task,) = runner.tasks
    assert "TASK: Implement this goal by following the approved plan." in task.prompt
    assert "GOAL: Add a subtract function to calc" in task.prompt and "PLAN:\n1. Add `subtract" in task.prompt


def test_a_failing_verdict_is_recorded_with_the_auditors_findings(rig):
    record = audit(rig, CannedRunner(RunResult(verdict_stdout("fail", findings=("skip marker",)), 0)), "case-09")

    assert (record["outcome"], record["label"], record["findings"]) == ("fail", "fail", ["skip marker"])


def test_a_timeout_is_an_infra_outcome_charged_at_the_assumed_cost_not_the_real_cost(rig):
    record = audit(rig, CannedRunner(RunResult("", 0, timed_out=True)))

    assert record["outcome"] == "infra:timeout" and record["cost_usd"] == 0 and record["assumed_usd"] == 0.6


def test_unparseable_output_is_an_infra_outcome(rig):
    assert audit(rig, CannedRunner(RunResult("not json", 1)))["outcome"] == "infra:unparseable"


def test_the_cost_is_this_runs_ledger_delta_so_an_earlier_attempt_is_not_counted_again(rig, tmp_path):
    audit(rig, CannedRunner(RunResult(verdict_stdout("pass", findings=(), cost=0.07), 0)))
    manager, store, ledger, eval_dir = rig
    (eval_dir / "case-01-r1.json").unlink()  # a resume re-runs it: same task id, same ledger, a fresh base repo
    fresh = WorktreeManager(build_base_repo(tmp_path / "again"), tmp_path / "wt2")

    second = run_one(CASES[0], 1, fresh, store, ledger, CannedRunner(RunResult(verdict_stdout("pass", findings=(), cost=0.02), 0)), 5.0, eval_dir)

    assert second["cost_usd"] == 0.02 and ledger.total() == pytest.approx(0.09)


def test_an_agent_that_changes_the_worktree_is_a_separate_outcome(rig):
    runner = CannedRunner(RunResult(verdict_stdout("pass", findings=()), 0),
                          before=lambda worktree: (worktree / "target" / "stray.txt").write_text("x"))

    assert audit(rig, runner)["outcome"] == "audit_modified_worktree"


def test_a_diff_over_the_size_limit_is_a_separate_outcome_and_the_agent_never_runs(rig):
    from dataclasses import replace
    from factory.audit_task import MAX_DIFF_BYTES
    manager, store, ledger, eval_dir = rig
    huge = replace(CASES[0], files={"src/calc/big.py": "x = 1\n" * (MAX_DIFF_BYTES // 4)})
    runner = CannedRunner(RunResult(verdict_stdout("pass", findings=()), 0))

    record = run_one(huge, 1, manager, store, ledger, runner, 5.0, eval_dir)

    assert record["outcome"] == "diff_too_large" and runner.tasks == []


def test_the_worktree_is_removed_and_nothing_is_written_when_the_budget_cap_stops_the_run(rig, tmp_path):
    from factory.step import BudgetCapReached
    with pytest.raises(BudgetCapReached):
        audit(rig, CannedRunner(RunResult(verdict_stdout("pass"), 0)), cap=0.1)  # 0.30 audit budget > cap

    assert not (tmp_path / "wt" / "a1-case-01").exists() and not (rig[3] / "case-01-r1.json").exists()


def test_the_result_is_written_through_a_temporary_file_so_a_kill_cannot_leave_half_a_result(rig, monkeypatch):
    replaced = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (replaced.append((Path(a).name, Path(b).name)), real(a, b)))

    audit(rig, CannedRunner(RunResult(verdict_stdout("pass", findings=()), 0)))

    assert [r for r in replaced if r[1] == "case-01-r1.json"] == [(".case-01-r1.json.tmp", "case-01-r1.json")]
    assert sorted(path.name for path in rig[3].glob("*.json")) == ["case-01-r1.json"]
