from factory.process_registry import Record, load_records, write_record


def record(**overrides):
    fields = dict(pid=4242, pgid=4242, task_id="t1", stage="implement", launched_after=100.0, launched_before=100.5)
    return Record(**{**fields, **overrides})


def test_a_written_record_is_read_back(tmp_path):
    write_record(tmp_path, record())

    assert load_records(tmp_path) == [record()]


def test_nothing_recorded_reads_as_empty(tmp_path):
    assert load_records(tmp_path) == []


def test_a_half_written_temp_file_is_not_a_record(tmp_path):
    write_record(tmp_path, record())
    (tmp_path / "procs" / ".9999.tmp").write_text('{"pid": 99')

    assert load_records(tmp_path) == [record()]
