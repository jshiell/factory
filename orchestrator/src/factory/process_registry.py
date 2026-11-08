import calendar
import json
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple


class Record(NamedTuple):
    pid: int
    pgid: int
    task_id: str
    stage: str
    launched_after: float
    launched_before: float


class InvalidRecord(NamedTuple):
    path: Path
    reason: str


def _procs_dir(state_dir) -> Path:
    return Path(state_dir) / "procs"


def write_record(state_dir, record: Record) -> None:
    """Write atomically (temp file, then rename) so a crash never leaves a truncated record."""
    directory = _procs_dir(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    temp = directory / f".{record.pid}.tmp"
    temp.write_text(json.dumps(record._asdict()))
    os.replace(temp, directory / f"{record.pid}.json")


def load_records(state_dir) -> list[Record | InvalidRecord]:
    directory = _procs_dir(state_dir)
    if not directory.is_dir():
        return []
    return [_read(path) for path in sorted(directory.glob("*.json"))]


def _read(path: Path) -> Record | InvalidRecord:
    try:
        record = Record(**json.loads(path.read_text()))
    except (ValueError, TypeError) as error:
        return InvalidRecord(path, f"unreadable: {error}")
    if record.pid <= 1 or record.pgid != record.pid or record.pgid == os.getpgrp():
        return InvalidRecord(path, "pid and pgid do not name a process group this factory launched")
    return record


EMPTY_POLL_INTERVAL_S, EMPTY_POLLS = 0.1, 30  # wait up to three seconds for a killed group to go
WINDOW_SLACK_S = 2.0  # `ps` start times have 1-second resolution


class ReapResult(NamedTuple):
    killed: list[Record]
    discarded: list[InvalidRecord]


class ProbeFailed(Exception):
    """The process table could not be read: the process may be alive or not."""


class ReapUncertain(Exception):
    def __init__(self, killed: list[Record], uncertain: list[tuple[Record, str]]):
        self.killed = killed
        detail = "; ".join(f"pid {record.pid} (task {record.task_id}, {record.stage}): {why}" for record, why in uncertain)
        super().__init__(f"cannot prove these processes dead: {detail}")


def reap_orphans(state_dir, probe, kill, group_alive, sleep=time.sleep) -> ReapResult:
    """Kill each recorded process group whose original process is still alive; the caller holds the state lock.

    `probe(pid)` gives the process's start time (epoch seconds) or None if there is no such process, and raises
    `ProbeFailed` if it cannot tell. A record whose probe failed is kept and reported in `ReapUncertain`."""
    killed, discarded, uncertain = [], [], []
    for record in load_records(state_dir):
        if isinstance(record, InvalidRecord):
            record.path.unlink()
            discarded.append(record)
            continue
        try:
            started = probe(record.pid)
        except ProbeFailed as error:
            uncertain.append((record, str(error)))
            continue
        if _is_ours_and_alive(record, started, group_alive):
            kill(record.pid)
            if not _group_emptied(record, group_alive, sleep):
                uncertain.append((record, "its group still has live processes after the kill"))
                continue
            killed.append(record)
        _record_path(state_dir, record.pid).unlink()
    if uncertain:
        raise ReapUncertain(killed, uncertain)
    return ReapResult(killed, discarded)


def _record_path(state_dir, pid: int) -> Path:
    return _procs_dir(state_dir) / f"{pid}.json"


def _in_window(record: Record, started: float) -> bool:
    return record.launched_after - WINDOW_SLACK_S <= started <= record.launched_before + WINDOW_SLACK_S


def _is_ours_and_alive(record: Record, started: float | None, group_alive) -> bool:
    if started is None:  # the leader is gone but members of its group may live (measured: the group stays signalable)
        return group_alive(record.pgid)
    return _in_window(record, started)


def _group_emptied(record: Record, group_alive, sleep) -> bool:
    for _ in range(EMPTY_POLLS):
        if not group_alive(record.pgid):
            return True
        sleep(EMPTY_POLL_INTERVAL_S)
    return not group_alive(record.pgid)


def ps_probe(run=subprocess.run):
    """A probe that reads a process's start time from `ps`, in a fixed locale and zone (the text depends on both)."""
    def probe(pid: int) -> float | None:
        env = {**os.environ, "LC_ALL": "C", "TZ": "UTC0"}
        try:
            ran = run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
        except OSError as error:
            raise ProbeFailed(f"cannot run ps: {error}") from error
        text = ran.stdout.strip()
        if ran.returncode == 1 and not text:
            return None
        if ran.returncode != 0:
            raise ProbeFailed(f"ps exited {ran.returncode}")
        try:
            return calendar.timegm(time.strptime(" ".join(text.split()), "%a %b %d %H:%M:%S %Y"))
        except ValueError as error:
            raise ProbeFailed(f"cannot read ps output {text!r}") from error
    return probe


class Tracker:
    """Records a launched process group for as long as the `with` block runs, so a later command can find an orphan."""

    def __init__(self, state_dir, task_id: str, stage: str):
        self._state_dir, self._task_id, self._stage = state_dir, task_id, stage

    @contextmanager
    def launched(self, pid: int, launched_after: float, launched_before: float):
        write_record(self._state_dir, Record(pid, pid, self._task_id, self._stage, launched_after, launched_before))
        try:
            yield
        finally:
            _record_path(self._state_dir, pid).unlink(missing_ok=True)
