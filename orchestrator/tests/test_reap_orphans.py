import pytest

from factory.process_registry import ProbeFailed, ReapUncertain, Record, load_records, reap_orphans, write_record


def record(**overrides):
    fields = dict(pid=4242, pgid=4242, task_id="t1", stage="implement", launched_after=100.0, launched_before=100.5)
    return Record(**{**fields, **overrides})


class Fakes:
    """Stand-ins for the process table: `started` maps pid to its start time (absent means gone)."""

    def __init__(self, started=None, group_alive=()):
        self.started, self.alive_groups, self.killed, self.sleeps = dict(started or {}), set(group_alive), [], []

    def probe(self, pid):
        return self.started.get(pid)

    def kill(self, pid):
        self.killed.append(pid)
        self.started.pop(pid, None)
        self.alive_groups.discard(pid)

    def group_alive(self, pgid):
        return pgid in self.alive_groups

    def reap(self, state_dir):
        return reap_orphans(state_dir, self.probe, self.kill, self.group_alive, sleep=self.sleeps.append)


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


def test_an_invalid_record_is_deleted_and_reported_and_nothing_is_killed(tmp_path):
    (tmp_path / "procs").mkdir()
    (tmp_path / "procs" / "1.json").write_text('{"pid": 1, "pgid": 1, "task_id": "t", "stage": "s", "launched_after": 0, "launched_before": 0}')
    fakes = Fakes(started={1: 0.0}, group_alive={1})

    result = fakes.reap(tmp_path)

    assert fakes.killed == [] and result.killed == []
    assert not (tmp_path / "procs" / "1.json").exists()
    assert [d.path.name for d in result.discarded] == ["1.json"]


def test_a_group_that_will_not_empty_after_the_kill_keeps_its_record_and_raises(tmp_path):
    write_record(tmp_path, record())
    fakes = Fakes(started={4242: 100.2}, group_alive={4242})
    fakes.kill = lambda pid: fakes.killed.append(pid)  # the kill has no effect on the fake group

    with pytest.raises(ReapUncertain) as raised:
        fakes.reap(tmp_path)

    assert [r.pid for r in load_records(tmp_path)] == [4242]
    assert "still has live processes" in str(raised.value)
    assert fakes.sleeps  # it polled before giving up


def test_guard_a_group_that_takes_a_moment_to_die_is_waited_for(tmp_path):  # starts green: pins the behaviour
    write_record(tmp_path, record())
    fakes = Fakes(started={4242: 100.2}, group_alive={4242})
    fakes.kill = lambda pid: fakes.killed.append(pid)
    fakes.sleeps = type("Dying", (list,), {"append": lambda self, s: fakes.alive_groups.discard(4242)})()

    result = fakes.reap(tmp_path)

    assert [r.pid for r in result.killed] == [4242] and load_records(tmp_path) == []
