import os
import signal
import subprocess
import time
from datetime import datetime

import pytest

from factory.cli import Context, _reap_with_the_real_process_table
from factory.job_store import JobStore
from factory.process_registry import ReapResult, ReapUncertain, Record
from factory.server import StartRefused, claim_state_dir
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_process_records import SqliteProcessRecords
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


def test_claiming_reaps_orphans_once_from_the_process_table_in_the_database(ctx):
    orphan = Record(4242, 4242, "t1", "implement", 100.0, 100.5)
    SqliteProcessRecords(ctx.settings.db_path).write(orphan)

    with claim_state_dir(ctx):
        pass

    (reaped,) = ctx.reaped
    assert reaped.load() == [orphan]


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


def _ps_works():
    try:
        return subprocess.run(["ps", "-o", "lstart=", "-p", str(os.getpid())], capture_output=True).returncode == 0
    except OSError:
        return False


@pytest.mark.skipif(not _ps_works(), reason="the process table cannot be read here (a sandbox blocks ps)")
def test_claiming_kills_a_live_orphan_recorded_in_the_database_and_empties_the_table(ctx):
    ctx.reap = _reap_with_the_real_process_table
    before = time.time()
    leader = subprocess.Popen(["sh", "-c", "sleep 60 & echo $!"], stdout=subprocess.PIPE, text=True, start_new_session=True)
    member = int(leader.stdout.readline())
    leader.wait()
    records = SqliteProcessRecords(ctx.settings.db_path)
    records.write(Record(leader.pid, leader.pid, "t1", "implement", before, time.time()))
    try:
        with claim_state_dir(ctx) as reaped:
            pass
        assert [record.pid for record in reaped.killed] == [leader.pid]
        assert records.load() == []
        with pytest.raises(ProcessLookupError):
            os.kill(member, 0)
    finally:
        try:
            os.kill(member, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.mark.parametrize("leftover", ["7.json", ".7.tmp"])
def test_old_file_records_stop_the_start_and_leave_jobs_alone_until_migrated(ctx, leftover):
    jobs = JobStore(ctx.settings.db_path)
    running = jobs.create("advance", "t")
    jobs.mark_running(running.id)
    (ctx.settings.state_dir / "procs").mkdir()
    (ctx.settings.state_dir / "procs" / leftover).write_text("{}")

    with pytest.raises(StartRefused, match="`factory migrate` first; nothing changed"):
        with claim_state_dir(ctx):
            pass

    assert ctx.reaped == [] and jobs.get(running.id).state == "running"


def test_guard_eval_records_and_an_empty_procs_directory_do_not_block_the_start(ctx):
    (ctx.settings.state_dir / "procs").mkdir()
    eval_procs = ctx.settings.state_dir / "evals" / "x" / "procs"
    eval_procs.mkdir(parents=True)
    (eval_procs / "1.json").write_text("{}")

    with claim_state_dir(ctx):
        pass

    assert len(ctx.reaped) == 1
