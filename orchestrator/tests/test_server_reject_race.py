from dataclasses import replace
from datetime import datetime

from factory.cli import Context
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore
from factory.task import Task, TaskState
from serving import served


class _MovesToQueuedAfterTheFirstLoad(SqliteTaskStore):
    moved = False

    def load(self, task_id):
        task = super().load(task_id)
        if not self.moved:
            self.moved = True
            self.save(replace(task, state=TaskState.QUEUED))
        return task


def test_a_json_reject_that_loses_the_race_to_an_approve_is_409_and_keeps_the_task_queued(tmp_path):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt")
    settings.state_dir.mkdir()
    store = _MovesToQueuedAfterTheFirstLoad(settings.db_path)
    ctx = Context(settings, store, SqliteLedger(settings.db_path), stages={}, clock=lambda: datetime(2026, 1, 2, 3, 4, 5))
    store.save(Task(id="a", prompt="", state=TaskState.REVIEWED, goal="g", plan="p"))

    with served(ctx) as client:
        response = client.post("/repos/proj/tasks/a/reject")

    assert response.status_code == 409
    assert SqliteTaskStore(settings.db_path).load("a").state is TaskState.QUEUED
