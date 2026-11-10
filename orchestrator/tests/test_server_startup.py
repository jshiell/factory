from datetime import datetime

import pytest

from factory.cli import Context
from factory.job_store import JobStore
from factory.process_registry import ReapResult, ReapUncertain
from factory.server import StartRefused, claim_state_dir
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore


@pytest.fixture
def ctx(tmp_path):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt")
    settings.state_dir.mkdir()
    context = Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={},
                      clock=lambda: datetime(2026, 1, 2, 3, 4, 5))
    context.reaped = []
    context.reap = lambda directory: context.reaped.append(directory) or ReapResult([], [])
    return context


def test_claiming_reaps_orphans_once_in_the_state_dir(ctx):
    with claim_state_dir(ctx):
        pass

    assert ctx.reaped == [ctx.settings.state_dir]


def test_a_second_claim_on_the_same_state_dir_is_refused_and_reaps_nothing(ctx):
    with claim_state_dir(ctx):
        with pytest.raises(StartRefused, match="another factory run holds the lock"):
            with claim_state_dir(ctx):
                pass

    assert len(ctx.reaped) == 1


def test_the_lock_is_released_when_the_claim_ends(ctx):
    with claim_state_dir(ctx):
        pass

    with claim_state_dir(ctx):
        pass


def test_claiming_marks_jobs_the_last_server_left_unfinished_as_interrupted(ctx):
    jobs = JobStore(ctx.settings.db_path)
    running, queued = jobs.create("advance", "t"), jobs.create("merge", "t")
    jobs.mark_running(running.id)
    done = jobs.create("abandon", "t")
    jobs.mark_succeeded(done.id, {})

    with claim_state_dir(ctx):
        pass

    assert [jobs.get(j.id).state for j in (running, queued, done)] == ["interrupted", "interrupted", "succeeded"]


def test_agents_that_cannot_be_proved_dead_stop_the_start_and_leave_jobs_alone(ctx):
    jobs = JobStore(ctx.settings.db_path)
    running = jobs.create("advance", "t")
    jobs.mark_running(running.id)

    class Record:
        pid, task_id, stage = 7, "t", "implement"

    def uncertain(directory):
        raise ReapUncertain([], [(Record, "ps: operation not permitted")])

    ctx.reap = uncertain

    with pytest.raises(StartRefused, match="cannot prove these processes dead"):
        with claim_state_dir(ctx):
            pass

    assert jobs.get(running.id).state == "running"
