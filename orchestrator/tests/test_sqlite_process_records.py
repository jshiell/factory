import os

from factory.process_registry import InvalidRecord, Record, Tracker, reap_orphans
from factory.sqlite_process_records import SqliteProcessRecords


def record(**overrides):
    fields = dict(pid=4242, pgid=4242, task_id="t1", stage="implement", launched_after=100.0, launched_before=100.5)
    return Record(**{**fields, **overrides})


def test_a_written_record_is_read_back_and_nothing_recorded_reads_as_empty(tmp_path):
    records = SqliteProcessRecords(tmp_path / "factory.db")
    assert records.load() == []

    records.write(record())

    assert records.load() == [record()]


def test_a_record_that_could_hit_the_wrong_process_is_invalid(tmp_path):
    records = SqliteProcessRecords(tmp_path / "factory.db")
    for bad in (record(pid=1, pgid=1), record(pid=4242, pgid=4243), record(pid=os.getpgrp(), pgid=os.getpgrp())):
        records.write(bad)

    found = records.load()

    assert all(isinstance(f, InvalidRecord) for f in found) and len(found) == 3


def test_reap_orphans_kills_a_live_orphan_and_discards_invalid_rows_from_the_table(tmp_path):
    records = SqliteProcessRecords(tmp_path / "factory.db")
    records.write(record())
    records.write(record(pid=1, pgid=1))
    killed = []
    alive = {4242}
    result = reap_orphans(
        records, probe=lambda pid: 100.2, kill=lambda pid: (killed.append(pid), alive.discard(pid)),
        group_alive=lambda pgid: pgid in alive, sleep=lambda s: None,
    )

    assert killed == [4242]
    assert [r.pid for r in result.killed] == [4242] and len(result.discarded) == 1
    assert records.load() == []


def test_a_tracker_records_the_group_only_while_the_block_runs(tmp_path):
    records = SqliteProcessRecords(tmp_path / "factory.db")

    with Tracker(records, "t1", "implement").launched(4242, 100.0, 100.5):
        assert records.load() == [record()]

    assert records.load() == []
