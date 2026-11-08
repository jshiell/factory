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


def _procs_dir(state_dir) -> Path:
    return Path(state_dir) / "procs"


def write_record(state_dir, record: Record) -> None:
    """Write atomically (temp file, then rename) so a crash never leaves a truncated record."""
    directory = _procs_dir(state_dir)
    directory.mkdir(parents=True, exist_ok=True)
    temp = directory / f".{record.pid}.tmp"
    temp.write_text(json.dumps(record._asdict()))
    os.replace(temp, directory / f"{record.pid}.json")


def load_records(state_dir) -> list[Record]:
    directory = _procs_dir(state_dir)
    if not directory.is_dir():
        return []
    return [Record(**json.loads(path.read_text())) for path in sorted(directory.glob("*.json"))]
