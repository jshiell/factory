from types import SimpleNamespace

import pytest

from factory.step import BudgetCapReached, MissingBudget
from factory.store import TaskStore
from factory.task import Task, TaskState
from factory.web_actions import CAP_REACHED, NO_BUDGET, WebActions


class Submissions:
    def __init__(self):
        self.jobs = []

    def submit(self, kind, task_id, run):
        self.jobs.append((kind, task_id, run))
        return self.jobs[-1]


def actions_with(tmp_path, diagnose):
    (tmp_path / "tasks").mkdir()
    store = TaskStore(tmp_path / "tasks")
    store.save(Task(id="t1", prompt="p", state=TaskState.FAILED, failure_reason="tests_failed"))
    ctx = SimpleNamespace(store=store, stages={"diagnose": diagnose})
    submissions = Submissions()
    return WebActions(ctx, submissions, merge=None, rebase=None, retry_with_plan=None), submissions


def test_a_queued_diagnosis_runs_the_diagnose_stage_and_reports_the_task_state(tmp_path):
    diagnosed = Task(id="t1", prompt="p", state=TaskState.FAILED, failure_reason="tests_failed")
    actions, submissions = actions_with(tmp_path, lambda task_id: diagnosed)

    actions.enqueue_diagnose("t1")

    kind, task_id, run = submissions.jobs[0]
    assert (kind, task_id) == ("diagnose", "t1")
    assert run() == {"state": "failed", "failure_reason": "tests_failed"}


@pytest.mark.parametrize("error,message", [(BudgetCapReached("t1"), CAP_REACHED), (MissingBudget("t1"), NO_BUDGET)])
def test_a_diagnosis_the_budget_stops_ends_the_job_with_a_readable_message(tmp_path, error, message):
    def stage(task_id):
        raise error

    actions, submissions = actions_with(tmp_path, stage)
    actions.enqueue_diagnose("t1")

    with pytest.raises(RuntimeError) as raised:
        submissions.jobs[0][2]()

    assert str(raised.value) == message
    assert "t1" not in message
