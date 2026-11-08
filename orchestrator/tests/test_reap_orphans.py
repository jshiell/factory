import pytest

from factory.process_registry import ProbeFailed, ReapUncertain, Record, load_records, reap_orphans, write_record


def record(**overrides):
    fields = dict(pid=4242, pgid=4242, task_id="t1", stage="implement", launched_after=100.0, launched_before=100.5)
    return Record(**{**fields, **overrides})


class Fakes:
    """Stand-ins for the process table: `started` maps pid to its start time (absent means gone)."""

    def __init__(self, started=None, group_alive=()):
        self.started, self.alive_groups, self.killed = dict(started or {}), set(group_alive), []

    def probe(self, pid):
        return self.started.get(pid)

    def kill(self, pid):
        self.killed.append(pid)
        self.started.pop(pid, None)
        self.alive_groups.discard(pid)

    def group_alive(self, pgid):
        return pgid in self.alive_groups

    def reap(self, state_dir):
        return reap_orphans(state_dir, self.probe, self.kill, self.group_alive)


def test_an_alive_process_started_inside_its_launch_window_is_killed_deleted_and_reported(tmp_path):
    write_record(tmp_path, record())
    fakes = Fakes(started={4242: 100.2}, group_alive={4242})

    result = fakes.reap(tmp_path)

    assert fakes.killed == [4242]
    assert load_records(tmp_path) == []
    assert [(r.pid, r.task_id, r.stage) for r in result.killed] == [(4242, "t1", "implement")]


def test_an_alive_process_started_outside_its_window_is_a_reused_pid_so_only_the_record_goes(tmp_path):
    write_record(tmp_path, record())
    fakes = Fakes(started={4242: 5000.0}, group_alive={4242})

    result = fakes.reap(tmp_path)

    assert fakes.killed == [] and result.killed == []
    assert load_records(tmp_path) == []


def test_guard_a_gone_process_with_an_empty_group_only_loses_its_record(tmp_path):  # starts green: pins the behaviour
    write_record(tmp_path, record())
    fakes = Fakes()

    result = fakes.reap(tmp_path)

    assert fakes.killed == [] and result.killed == []
    assert load_records(tmp_path) == []


def test_a_dead_leader_with_a_live_group_has_the_group_killed(tmp_path):
    write_record(tmp_path, record())
    fakes = Fakes(group_alive={4242})

    result = fakes.reap(tmp_path)

    assert fakes.killed == [4242]
    assert load_records(tmp_path) == []
    assert [r.pid for r in result.killed] == [4242]


def test_a_failed_probe_keeps_its_record_and_raises_but_the_other_orphans_are_still_reaped(tmp_path):
    write_record(tmp_path, record(pid=4242, pgid=4242))
    write_record(tmp_path, record(pid=4343, pgid=4343, task_id="t2"))
    fakes = Fakes(started={4343: 100.2}, group_alive={4343})
    probe = fakes.probe

    def flaky(pid):
        if pid == 4242:
            raise ProbeFailed("ps: operation not permitted")
        return probe(pid)

    with pytest.raises(ReapUncertain) as raised:
        reap_orphans(tmp_path, flaky, fakes.kill, fakes.group_alive)

    assert [r.pid for r in load_records(tmp_path)] == [4242]
    assert [r.pid for r in raised.value.killed] == [4343]
    assert "4242" in str(raised.value) and "ps: operation not permitted" in str(raised.value)
