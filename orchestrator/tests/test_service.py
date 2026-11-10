from datetime import datetime

import pytest

from factory import service
from factory.digest import plan_digest
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


def test_create_task_stores_a_proposed_task_under_a_timestamp_id(store):
    task = service.create_task(store, lambda: datetime(2026, 1, 2, 3, 4, 5), "add multiply")

    assert task.id == "20260102-030405"
    assert store.load(task.id).goal == "add multiply"
    assert store.load(task.id).state is TaskState.PROPOSED


def test_reject_plan_returns_a_reviewed_task_to_proposed(store):
    store.save(Task(id="a", prompt="", state=TaskState.REVIEWED, plan="p"))

    task = service.reject_plan(store, "a")

    assert task.id == "a"
    assert store.load("a").state is not TaskState.REVIEWED


def test_reject_plan_on_a_task_not_reviewed_raises_with_the_task(store):
    store.save(Task(id="a", prompt="", state=TaskState.PROPOSED))

    with pytest.raises(service.NotReviewed) as raised:
        service.reject_plan(store, "a")

    assert raised.value.task.id == "a"
    assert store.load("a").state is TaskState.PROPOSED


def reviewed(store, plan="1. do it"):
    task = Task(id="a", prompt="", state=TaskState.REVIEWED, goal="g", plan=plan, plan_digest=plan_digest(plan),
                review_findings=("note",))
    store.save(task)
    return task


def test_approve_queues_a_reviewed_task_whose_digest_was_shown(store):
    task = reviewed(store)

    approved = service.approve(store, "a", plan_digest(task.plan), task.review_findings)

    assert approved.state is TaskState.QUEUED
    assert store.load("a").state is TaskState.QUEUED


def test_approve_with_a_stale_digest_raises_plan_changed_and_changes_nothing(store):
    task = reviewed(store)

    with pytest.raises(service.PlanChanged):
        service.approve(store, "a", plan_digest("another plan"), task.review_findings)

    assert store.load("a").state is TaskState.REVIEWED


def test_approve_on_a_task_not_reviewed_raises_not_reviewed(store):
    store.save(Task(id="a", prompt="", state=TaskState.PROPOSED))

    with pytest.raises(service.NotReviewed):
        service.approve(store, "a", plan_digest(""), ())
