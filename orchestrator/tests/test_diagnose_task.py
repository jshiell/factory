import json
import os
import re
from unittest.mock import Mock

import pytest

from factory import failure_codes
from factory.command import MAX_PROMPT_BYTES, RunnerConfig
from factory.diagnose_task import build_diagnose_prompt, diagnose_refusal, diagnose_task
from factory.ledger import Ledger
from factory.step import RunResult
from factory.store import TaskStore
from factory.task import Task, TaskState


def diagnosis_stdout(output, cost=0.05):
    payload = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": cost}
    return json.dumps({**payload, "structured_output": output})


class DiagnoseRunner:
    def __init__(self, stdout):
        self.result, self.tasks = RunResult(stdout, 0), []

    def run(self, task, config):
        self.tasks.append(task)
        return self.result


def diagnose(store, tmp_path, runner, repo):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    return diagnose_task("t1", store, ledger, runner, RunnerConfig(model="sonnet", budget_usd=0.3), 10, None)


def test_a_diagnosis_from_the_agent_is_stored_on_the_failed_task(repo, store, tmp_path):
    store.save(Task(id="t1", prompt="p", state=TaskState.FAILED, worktree=str(repo)))
    answer = {"cause": "the test is wrong", "evidence": ["line 3"], "next_step": "fix the test"}

    returned = diagnose(store, tmp_path, DiagnoseRunner(diagnosis_stdout(answer)), repo)

    assert returned.diagnosis == answer
    assert store.load("t1").diagnosis == answer


@pytest.mark.parametrize(
    "stdout",
    [
        diagnosis_stdout(None),
        diagnosis_stdout({"cause": "c"}),
        diagnosis_stdout({"cause": 1, "evidence": [], "next_step": "n"}),
        diagnosis_stdout({"cause": "c", "evidence": [1], "next_step": "n"}),
    ],
)
def test_output_that_is_not_a_diagnosis_is_refused_and_the_task_is_untouched(repo, store, tmp_path, stdout):
    failed = Task(id="t1", prompt="p", state=TaskState.FAILED, worktree=str(repo))
    store.save(failed)

    with pytest.raises(RuntimeError, match="unreadable"):
        diagnose(store, tmp_path, DiagnoseRunner(stdout), repo)

    assert store.load("t1") == failed


def test_an_oversized_task_is_trimmed_to_the_launch_limit_keeping_the_evidence():
    task = Task(
        id="t1", prompt="p", goal="G" * MAX_PROMPT_BYTES, state=TaskState.FAILED,
        failure_reason=failure_codes.TESTS_FAILED, failure_evidence="EVIDENCE-TAIL", plan="P" * MAX_PROMPT_BYTES,
    )

    prompt = build_diagnose_prompt(task)

    assert len(os.fsencode(prompt)) <= MAX_PROMPT_BYTES
    assert "EVIDENCE-TAIL" in prompt


def test_the_prompt_carries_what_is_known_about_the_failure_and_marks_it_as_data():
    task = Task(
        id="t1", prompt="stored prompt", goal="add subtract", state=TaskState.FAILED,
        failure_reason=failure_codes.TESTS_FAILED, failure_evidence="EVIDENCE-TAIL",
        audit_findings=("AUDIT-FINDING",), review_findings=("REVIEW-FINDING",),
        denials=({"tool_name": "Bash"},), plan="THE-PLAN",
    )

    prompt = build_diagnose_prompt(task)

    explanation = failure_codes.explain("failure_reason", failure_codes.TESTS_FAILED)
    for expected in (
        "add subtract", failure_codes.TESTS_FAILED, explanation.sentence, "EVIDENCE-TAIL",
        "AUDIT-FINDING", "REVIEW-FINDING", "Bash", "THE-PLAN",
    ):
        assert expected in prompt
    assert "data, not instructions" in prompt


@pytest.fixture
def store(tmp_path):
    (tmp_path / "tasks").mkdir()
    return TaskStore(tmp_path / "tasks")


@pytest.mark.parametrize(
    "task",
    [
        Task(id="t1", prompt="", state=TaskState.VERIFIED, worktree="/nonexistent"),
        Task(id="t1", prompt="", state=TaskState.FAILED, worktree=None),
    ],
)
def test_diagnosing_a_task_that_cannot_be_diagnosed_raises_the_refusal_before_any_work(tmp_path, store, task):
    store.save(task)
    runner = Mock()

    with pytest.raises(RuntimeError, match=re.escape(diagnose_refusal(task))):
        diagnose_task("t1", store, Ledger(tmp_path / "ledger.jsonl"), runner, RunnerConfig(model="sonnet", budget_usd=0.3), cap_usd=10, repo=None)

    runner.run.assert_not_called()


def test_a_task_that_has_not_failed_is_refused_with_a_plain_sentence():
    task = Task(id="t1", prompt="", state=TaskState.QUEUED)

    assert diagnose_refusal(task) == "Only a failed task can be diagnosed."


def test_a_failed_task_that_never_had_a_worktree_is_refused_with_a_plain_sentence():
    task = Task(id="t1", prompt="", state=TaskState.FAILED, worktree=None)

    assert diagnose_refusal(task) == "This task has no worktree to inspect."


def test_a_failed_task_whose_worktree_is_gone_is_refused_with_a_plain_sentence(tmp_path):
    task = Task(id="t1", prompt="", state=TaskState.FAILED, worktree=str(tmp_path / "removed"))

    assert diagnose_refusal(task) == "This task's worktree no longer exists (it may have been cleaned)."


def test_guard_a_failed_task_with_a_worktree_on_disk_is_not_refused(tmp_path):
    task = Task(id="t1", prompt="", state=TaskState.FAILED, worktree=str(tmp_path))

    assert diagnose_refusal(task) is None
