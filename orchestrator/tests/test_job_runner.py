import threading

import pytest

from factory.job_runner import JobRunner
from factory.job_store import JobStore


@pytest.fixture
def jobs(tmp_path):
    return JobStore(tmp_path / "factory.db")


@pytest.fixture
def runner(jobs):
    runner = JobRunner(jobs)
    runner.start()
    yield runner
    runner.stop()


def test_a_submitted_job_runs_and_records_its_result(runner, jobs):
    job = runner.submit("advance", "t1", lambda: {"steps": 2})

    runner.wait_idle()

    done = jobs.get(job.id)
    assert (done.state, done.result) == ("succeeded", {"steps": 2})


def test_a_job_that_raises_is_failed_with_the_message_and_the_worker_survives(runner, jobs):
    def boom():
        raise RuntimeError("kaboom")

    bad = runner.submit("advance", "t1", boom)
    good = runner.submit("advance", "t2", lambda: {"ok": True})

    runner.wait_idle()

    assert (jobs.get(bad.id).state, jobs.get(bad.id).error) == ("failed", "kaboom")
    assert jobs.get(good.id).state == "succeeded"


def test_jobs_run_one_at_a_time_in_submission_order(runner, jobs):
    release, order = threading.Event(), []
    first = runner.submit("advance", "a", lambda: (release.wait(5), order.append("a"), {})[2])
    second = runner.submit("merge", "b", lambda: (order.append("b"), {})[1])

    assert jobs.get(second.id).state == "queued"
    release.set()
    runner.wait_idle()

    assert order == ["a", "b"]
    assert (jobs.get(first.id).state, jobs.get(second.id).state) == ("succeeded", "succeeded")
