import pytest

from factory.ledger import Ledger
from factory.recover_task import RecoverRefused, recover_task
from factory.store import TaskStore
from factory.task import Task, TaskState


@pytest.fixture
def setup(tmp_path):
    (tmp_path / "tasks").mkdir()
    return TaskStore(tmp_path / "tasks"), Ledger(tmp_path / "ledger.jsonl")


@pytest.mark.parametrize("state", [s for s in TaskState if s is not TaskState.RUNNING])
def test_recover_refuses_a_task_that_is_not_running(setup, state):
    store, ledger = setup
    store.save(Task(id="t1", prompt="", state=state))

    with pytest.raises(RecoverRefused, match=state.value):
        recover_task("t1", store, ledger, budget_usd=0.4)

    assert store.load("t1").state is state and ledger.entries() == []


def test_a_running_task_becomes_failed_as_interrupted(setup):
    store, ledger = setup
    store.save(Task(id="t1", prompt="", state=TaskState.RUNNING, attempts=1))

    task = recover_task("t1", store, ledger, budget_usd=0.4)

    assert (task.state, task.failure_reason) == (TaskState.FAILED, "interrupted")
    assert store.load("t1") == task


def test_recovery_appends_the_assumed_cost_of_the_interrupted_run(setup):
    store, ledger = setup
    store.save(Task(id="t1", prompt="", state=TaskState.RUNNING))

    recover_task("t1", store, ledger, budget_usd=0.4)

    assert ledger.entries() == [{"task_id": "t1", "cost_usd": 0.8, "assumed": True, "stage": "implement"}]
