import time

from factory.process_registry import FileProcessRecords

# The record is written after the process is launched, so a child that reads it must wait for it.
WAIT_FOR_RECORD = 'for _ in $(seq 100); do ls "$STATE/procs"/*.json >/dev/null 2>&1 && break; sleep 0.05; done; '


class RecordsWrittenLate(FileProcessRecords):
    """File records whose write lags the launch, so the race a fast child can win happens every time."""

    def __init__(self, state_dir, delay_s: float = 0.5):
        super().__init__(state_dir)
        self._delay_s = delay_s

    def write(self, record) -> None:
        time.sleep(self._delay_s)
        super().write(record)
