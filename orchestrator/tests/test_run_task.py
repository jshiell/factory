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


from gitutil import git

from factory.run_task import WorktreeNotPristine


def planned_worktree_task(store, repo, base_sha=None):
    store.save(Task(id="t1", prompt="do it", worktree=str(repo),
                    base_sha=base_sha or git(repo, "rev-parse", "HEAD")))


def test_a_task_with_a_pristine_worktree_reuses_it_without_creating_one(repo, store, ledger):
    planned_worktree_task(store, repo)
    worktrees, runner = FakeWorktrees(), RecordingRunner(store)

    run_task("t1", store, ledger, runner, CONFIG, cap_usd=10, worktrees=worktrees)

    assert worktrees.created == []
    assert runner.seen[0].worktree == str(repo)
    assert runner.seen[0].state is TaskState.RUNNING


def mutate_head(repo):
    (repo / "b.txt").write_text("b")
    git(repo, "add", "b.txt")
    git(repo, "commit", "-q", "-m", "moved")


def mutate_dirty(repo):
    (repo / "stray.txt").write_text("s")


@pytest.mark.parametrize("mutate", [mutate_head, mutate_dirty])
def test_a_reused_worktree_that_moved_or_is_dirty_is_refused_and_the_task_stays_queued(
    repo, store, ledger, mutate
):
    planned_worktree_task(store, repo)
    mutate(repo)
    runner = RecordingRunner(store)

    with pytest.raises(WorktreeNotPristine):
        run_task("t1", store, ledger, runner, CONFIG, cap_usd=10, worktrees=FakeWorktrees())

    assert runner.seen == [] and store.load("t1").state is TaskState.QUEUED


def test_a_reused_worktree_that_no_longer_exists_is_refused(repo, store, ledger, tmp_path):
    store.save(Task(id="t1", prompt="do it", worktree=str(tmp_path / "gone"), base_sha="abc"))
    runner = RecordingRunner(store)

    with pytest.raises(WorktreeNotPristine):
        run_task("t1", store, ledger, runner, CONFIG, cap_usd=10, worktrees=FakeWorktrees())

    assert runner.seen == []
