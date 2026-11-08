import json
import os
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


def reap_orphans(state_dir, probe, kill, group_alive) -> ReapResult:
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
