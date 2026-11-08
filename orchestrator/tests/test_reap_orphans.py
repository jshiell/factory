import pytest

from factory.process_registry import Record, load_records, reap_orphans, write_record


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
