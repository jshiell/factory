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
