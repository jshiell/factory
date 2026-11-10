import queue
import threading
from collections.abc import Callable

from factory.job_store import Job, JobStore


class JobRunner:
    """One worker thread running submitted jobs one at a time, in submission order."""

    def __init__(self, jobs: JobStore):
        self._jobs = jobs
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._work, name="factory-jobs", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=5)

    def submit(self, kind: str, task_id: str | None, run: Callable[[], dict]) -> Job:
        job = self._jobs.create(kind, task_id)
        self._queue.put((job.id, run))
        return job

    def wait_idle(self) -> None:
        self._queue.join()

    def _work(self) -> None:
        while (item := self._queue.get()) is not None:
            job_id, run = item
            try:
                self._jobs.mark_running(job_id)
                self._jobs.mark_succeeded(job_id, run())
            except Exception as error:  # a failed job must not stop the worker
                self._jobs.mark_failed(job_id, str(error))
            finally:
                self._queue.task_done()
        self._queue.task_done()
