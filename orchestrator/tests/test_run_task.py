from pathlib import Path

import pytest

from factory.command import RunnerConfig
from factory.ledger import Ledger
from factory.run_task import run_task
from factory.step import BudgetCapReached, RunResult
from factory.store import TaskStore
from factory.task import Task, TaskState

CONFIG = RunnerConfig(model="sonnet", budget_usd=0.5)


class FakeWorktrees:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.created = []

    def create(self, task_id: str):
        if self.fail:
            raise RuntimeError("git exploded")
        self.created.append(task_id)
        return Path("/w") / task_id, "sha123"


class RecordingRunner:
    def __init__(self, store: TaskStore):
        self._store = store
        self.seen: list[Task] = []

    def run(self, task, config):
        self.seen.append(self._store.load(task.id))
        return RunResult("", 1)


@pytest.fixture
def store(tmp_path):
    (tmp_path / "tasks").mkdir()
    store = TaskStore(tmp_path / "tasks")
    store.save(Task(id="t1", prompt="do it"))
    return store


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "ledger.jsonl")


def test_worktree_and_base_sha_are_persisted_before_the_runner_runs(store, ledger):
    runner = RecordingRunner(store)

    run_task("t1", store, ledger, runner, CONFIG, cap_usd=10, worktrees=FakeWorktrees())

    seen = runner.seen[0]
    assert seen.state is TaskState.RUNNING
    assert seen.worktree == "/w/t1"
    assert seen.base_sha == "sha123"


def test_worktree_creation_failure_leaves_the_task_queued_and_runner_unused(store, ledger):
    runner = RecordingRunner(store)

    with pytest.raises(RuntimeError):
        run_task("t1", store, ledger, runner, CONFIG, cap_usd=10, worktrees=FakeWorktrees(fail=True))

    assert runner.seen == []
    assert store.load("t1").state is TaskState.QUEUED


def test_refused_run_creates_no_worktree(store, ledger):
    ledger.append(task_id="earlier", cost_usd=9.9)
    worktrees = FakeWorktrees()

    with pytest.raises(BudgetCapReached):
        run_task("t1", store, ledger, RecordingRunner(store), CONFIG, cap_usd=10, worktrees=worktrees)

    assert worktrees.created == []
