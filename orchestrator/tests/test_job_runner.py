import sqlite3
import subprocess
import threading
import time

import pytest

from factory.job_runner import JobRunner
from factory.job_store import JobStore


@pytest.fixture
def jobs(tmp_path):
    return JobStore(tmp_path / "factory.db")


@pytest.fixture
def runner(jobs):
    runner = JobRunner()
    runner.start()
    yield runner
    runner.stop()


def test_a_submitted_job_runs_and_records_its_result(runner, jobs):
    job = runner.submit("advance", "t1", lambda: {"steps": 2}, jobs)

    runner.wait_idle()

    done = jobs.get(job.id)
    assert (done.state, done.result) == ("succeeded", {"steps": 2})


def test_a_job_that_raises_is_failed_with_the_message_and_the_worker_survives(runner, jobs):
    def boom():
        raise RuntimeError("kaboom")

    bad = runner.submit("advance", "t1", boom, jobs)
    good = runner.submit("advance", "t2", lambda: {"ok": True}, jobs)

    runner.wait_idle()

    assert (jobs.get(bad.id).state, jobs.get(bad.id).error) == ("failed", "kaboom")
    assert jobs.get(good.id).state == "succeeded"


def test_jobs_run_one_at_a_time_in_submission_order(runner, jobs):
    release, order = threading.Event(), []
    first = runner.submit("advance", "a", lambda: (release.wait(5), order.append("a"), {})[2], jobs)
    second = runner.submit("merge", "b", lambda: (order.append("b"), {})[1], jobs)

    assert jobs.get(second.id).state == "queued"
    release.set()
    runner.wait_idle()

    assert order == ["a", "b"]
    assert (jobs.get(first.id).state, jobs.get(second.id).state) == ("succeeded", "succeeded")


def test_a_job_error_with_a_lone_surrogate_is_still_recorded_and_the_worker_survives(runner, jobs):
    def boom():
        raise RuntimeError("bad \ud800 text")

    bad = runner.submit("advance", "t1", boom, jobs)
    good = runner.submit("advance", "t2", lambda: {"ok": True}, jobs)

    deadline = time.monotonic() + 5  # no wait_idle: a dead worker would block it forever
    while jobs.get(good.id).state != "succeeded" and time.monotonic() < deadline:
        time.sleep(0.01)

    assert jobs.get(bad.id).state == "failed"
    assert "bad" in jobs.get(bad.id).error
    assert jobs.get(good.id).state == "succeeded"


JOB_FAILED_SENTENCE = "Git or the system failed while running this job. The server log has the details."


@pytest.mark.parametrize("error, logged", [
    (subprocess.CalledProcessError(128, ["git", "branch", "secret-ref"], stderr="fatal: secret\nforged line"), "fatal: secret"),
    (FileNotFoundError(2, "No such file", "/secret/path"), "/secret/path"),
    (subprocess.TimeoutExpired(["git", "secret-ref"], 1), "secret-ref"),
], ids=["git failure", "os error", "timeout"])
def test_a_git_or_system_failure_is_failed_with_a_fixed_sentence_and_logged_escaped(runner, jobs, capsys, error, logged):
    def fail():
        raise error

    bad = runner.submit("merge", "t1", fail, jobs)
    good = runner.submit("advance", "t2", lambda: {"ok": True}, jobs)
    runner.wait_idle()

    assert (jobs.get(bad.id).state, jobs.get(bad.id).error) == ("failed", JOB_FAILED_SENTENCE)
    assert jobs.get(good.id).state == "succeeded"
    log_lines = capsys.readouterr().err.strip().splitlines()
    assert len(log_lines) == 1 and logged in log_lines[0]  # a newline in git's stderr cannot forge a second line


JOB_ERROR_SENTENCE = "The job failed unexpectedly. The server log has the details."


def test_an_unexpected_exception_is_failed_with_a_fixed_sentence_and_logged(runner, jobs, capsys):
    def fail():
        raise ValueError("plugin /secret/path contains a symlink")

    bad = runner.submit("eval", None, fail, jobs)
    good = runner.submit("advance", "t2", lambda: {"ok": True}, jobs)
    runner.wait_idle()

    assert (jobs.get(bad.id).state, jobs.get(bad.id).error) == ("failed", JOB_ERROR_SENTENCE)
    assert jobs.get(good.id).state == "succeeded"
    log_lines = capsys.readouterr().err.strip().splitlines()
    assert len(log_lines) == 1 and "ValueError" in log_lines[0] and "/secret/path" in log_lines[0]


class _FailsToRecordSuccess(JobStore):
    def mark_succeeded(self, job_id, result):
        raise ValueError("cannot write /secret/db")


def test_a_failed_result_write_is_failed_with_the_fixed_sentence_logged_and_the_worker_survives(tmp_path, capsys):
    jobs = _FailsToRecordSuccess(tmp_path / "factory.db")
    runner = JobRunner()
    runner.start()
    try:
        first = runner.submit("advance", "t1", lambda: {"ok": True}, jobs)
        second = runner.submit("advance", "t2", lambda: {"ok": True}, jobs)
        runner.wait_idle()
    finally:
        runner.stop()

    assert [jobs.get(job.id).error for job in (first, second)] == [JOB_ERROR_SENTENCE] * 2  # the worker ran both
    log_lines = capsys.readouterr().err.strip().splitlines()
    assert len(log_lines) == 2 and all("/secret/db" in line for line in log_lines)


class _FailsToRecordFailure(JobStore):
    def mark_failed(self, job_id, error):
        if self.get(job_id).task_id == "bad":
            raise sqlite3.OperationalError("database is locked")
        super().mark_failed(job_id, error)


def _raises(error):
    def run():
        raise error
    return run


@pytest.mark.parametrize("error", [
    OSError("disk"),
    RuntimeError("refused"),
    ValueError("odd"),
], ids=["os error", "refusal", "unexpected"])
def test_a_job_whose_failure_cannot_be_recorded_is_logged_and_the_worker_survives(tmp_path, capsys, error):
    jobs = _FailsToRecordFailure(tmp_path / "factory.db")
    runner = JobRunner()
    runner.start()
    try:
        bad = runner.submit("advance", "bad", _raises(error), jobs)
        good = runner.submit("advance", "t2", lambda: {"ok": True}, jobs)
        deadline = time.monotonic() + 5  # no wait_idle: a dead worker would block it forever
        while jobs.get(good.id).state != "succeeded" and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        runner.stop()

    assert jobs.get(good.id).state == "succeeded"
    assert jobs.get(bad.id).state == "running"  # the accepted limit: until the next start marks it interrupted
    recording = [line for line in capsys.readouterr().err.splitlines() if "recording the failure" in line]
    assert len(recording) == 1 and bad.id in recording[0] and "database is locked" in recording[0]


class _FailsToRecordFailureOnce(JobStore):
    def __init__(self, db_path):
        super().__init__(db_path)
        self._failed_once = False

    def mark_failed(self, job_id, error):
        if not self._failed_once:
            self._failed_once = True
            raise sqlite3.OperationalError("database is locked")
        super().mark_failed(job_id, error)


def test_a_system_failure_whose_first_recording_fails_is_failed_by_the_second_chance(tmp_path):
    jobs = _FailsToRecordFailureOnce(tmp_path / "factory.db")
    runner = JobRunner()
    runner.start()
    try:
        bad = runner.submit("advance", "t1", _raises(OSError("disk")), jobs)
        runner.wait_idle()
    finally:
        runner.stop()

    assert (jobs.get(bad.id).state, jobs.get(bad.id).error) == ("failed", JOB_ERROR_SENTENCE)


def test_a_job_submitted_with_its_own_store_is_recorded_and_finished_there(runner, jobs, tmp_path):
    other = JobStore(tmp_path / "other.db")

    job = runner.submit("advance", "t1", lambda: {"steps": 1}, other)

    runner.wait_idle()

    assert (other.get(job.id).state, other.get(job.id).result) == ("succeeded", {"steps": 1})
    assert jobs.list() == []
