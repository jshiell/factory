import queue
import subprocess
import threading
from collections.abc import Callable

from factory.job_store import Job, JobStore
from factory.textsafe import log_failure

JOB_FAILED_SENTENCE = "Git or the system failed while running this job. The server log has the details."
JOB_ERROR_SENTENCE = "The job failed unexpectedly. The server log has the details."


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

    def submit(self, kind: str, task_id: str | None, run: Callable[[], dict], jobs: JobStore | None = None) -> Job:
        store = jobs or self._jobs
        job = store.create(kind, task_id)
        self._queue.put((store, job.id, run))
        return job

    def wait_idle(self) -> None:
        self._queue.join()

    def _work(self) -> None:
        while (item := self._queue.get()) is not None:
            jobs, job_id, run = item
            try:
                try:
                    jobs.mark_running(job_id)
                    result = run()
                except (subprocess.SubprocessError, OSError) as error:  # git's command line and paths stay out of job.error
                    log_failure(f"job {job_id}", error)
                    jobs.mark_failed(job_id, JOB_FAILED_SENTENCE)
                else:
                    jobs.mark_succeeded(job_id, result)
            except RuntimeError as error:  # the refusal channel: its message is written for the user
                self._record_failure(jobs, job_id, str(error))
            except Exception as error:  # a failed job (or a failed result write) must not stop the worker
                log_failure(f"job {job_id}", error)
                self._record_failure(jobs, job_id, JOB_ERROR_SENTENCE)
            finally:
                self._queue.task_done()
        self._queue.task_done()

    def _record_failure(self, jobs: JobStore, job_id: str, message: str) -> None:
        try:
            jobs.mark_failed(job_id, message)
        except Exception as error:  # a store fault must not end the worker; the job stays unfinished until the next start
            log_failure(f"job {job_id}: recording the failure", error)
