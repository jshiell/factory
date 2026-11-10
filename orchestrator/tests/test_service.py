import pytest

from factory import service
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore
from factory.store import TaskNotFound
from factory.task import Task, TaskState


@pytest.fixture
def store(tmp_path):
    return SqliteTaskStore(tmp_path / "factory.db")


@pytest.fixture
def ledger(tmp_path):
    return SqliteLedger(tmp_path / "factory.db")


def test_cost_report_groups_the_ledger_by_stage_and_task(store, ledger):
    ledger.append("a", 0.25, stage="plan")
    ledger.append("a", 0.5, assumed=True, stage="implement")
    ledger.append("b", 0.25, stage="plan")

    report = service.cost_report(store, ledger)

    assert report.by_stage == {"plan": 0.5, "implement": 0.5}
    assert report.by_task == {"a": 0.75, "b": 0.25}
    assert report.assumed == 0.5
    assert report.total == 1.0


def test_cost_report_for_one_task_counts_only_its_entries(store, ledger):
    store.save(Task(id="a", prompt="", state=TaskState.PROPOSED))
    ledger.append("a", 0.25, stage="plan")
    ledger.append("b", 0.5, stage="plan")

    report = service.cost_report(store, ledger, "a")

    assert report.by_task == {"a": 0.25}
    assert report.total == 0.25


def test_cost_report_for_an_unknown_task_raises_not_found(store, ledger):
    with pytest.raises(TaskNotFound):
        service.cost_report(store, ledger, "nope")
