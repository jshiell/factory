import calendar
import os
import subprocess

import pytest

from factory.process_registry import InvalidRecord, ProbeFailed, Record, load_records, ps_probe, write_record


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


@pytest.mark.parametrize("bad", [
    record(pid=1, pgid=1), record(pid=0, pgid=0), record(pid=-5, pgid=-5),
    record(pid=4242, pgid=4243), record(pid=os.getpgrp(), pgid=os.getpgrp()),
])
def test_a_record_that_could_hit_the_wrong_process_is_invalid(tmp_path, bad):
    write_record(tmp_path, bad)

    (found,) = load_records(tmp_path)

    assert isinstance(found, InvalidRecord) and found.path == tmp_path / "procs" / f"{bad.pid}.json"


@pytest.mark.parametrize("text", ["{not json", "[]", '{"pid": 7}'])
def test_an_unreadable_or_incomplete_record_is_invalid(tmp_path, text):
    (tmp_path / "procs").mkdir()
    (tmp_path / "procs" / "7.json").write_text(text)

    (found,) = load_records(tmp_path)

    assert isinstance(found, InvalidRecord)


class FakePs:
    def __init__(self, stdout="", returncode=0, error=None):
        self.stdout, self.returncode, self.error, self.calls = stdout, returncode, error, []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.error:
            raise self.error
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


def test_ps_start_time_is_read_as_utc_epoch_seconds_with_a_fixed_locale_and_zone():
    ps = FakePs("Sun  8 Nov 09:54:32 2026\n")

    started = ps_probe(ps)(18875)

    assert started == calendar.timegm((2026, 11, 8, 9, 54, 32))
    argv, kwargs = ps.calls[0]
    assert argv == ["ps", "-o", "lstart=", "-p", "18875"]
    assert kwargs["env"]["LC_ALL"] == "C" and kwargs["env"]["TZ"] == "UTC0"


def test_no_such_process_is_none():
    assert ps_probe(FakePs("", returncode=1))(18875) is None


@pytest.mark.parametrize("ps", [
    FakePs(error=PermissionError("operation not permitted: 'ps'")), FakePs(error=FileNotFoundError("ps")),
    FakePs("garbage\n"), FakePs("", returncode=2),
])
def test_a_probe_that_cannot_tell_raises_probe_failed(ps):
    with pytest.raises(ProbeFailed):
        ps_probe(ps)(18875)
