import pytest

from factory.job_store import JobNotFound, JobStore


@pytest.fixture
def jobs(tmp_path):
    return JobStore(tmp_path / "factory.db")


def test_a_new_job_is_queued_and_can_be_read_back(jobs):
    job = jobs.create("advance", "t1")

    assert (job.kind, job.task_id, job.state, job.result, job.error) == ("advance", "t1", "queued", None, None)
    assert jobs.get(job.id) == job


def test_unknown_job_raises_not_found(jobs):
    with pytest.raises(JobNotFound):
        jobs.get("nope")


def test_a_job_moves_through_running_to_succeeded_with_a_result(jobs):
    job = jobs.create("advance", "t1")

    jobs.mark_running(job.id)
    assert jobs.get(job.id).state == "running"
    jobs.mark_succeeded(job.id, {"steps": ["plan -> planned"]})

    done = jobs.get(job.id)
    assert (done.state, done.result) == ("succeeded", {"steps": ["plan -> planned"]})


def test_a_failed_job_keeps_its_error(jobs):
    job = jobs.create("merge", "t1")

    jobs.mark_failed(job.id, "merge refused: nope")

    failed = jobs.get(job.id)
    assert (failed.state, failed.error) == ("failed", "merge refused: nope")


def test_list_returns_jobs_in_creation_order(jobs):
    first, second = jobs.create("advance", "a"), jobs.create("merge", "b")

    assert [j.id for j in jobs.list()] == [first.id, second.id]


def test_interrupt_unfinished_marks_queued_and_running_jobs_only(jobs):
    queued, running, done = jobs.create("a", "t"), jobs.create("b", "t"), jobs.create("c", "t")
    jobs.mark_running(running.id)
    jobs.mark_succeeded(done.id, {})

    jobs.interrupt_unfinished()

    assert [jobs.get(j.id).state for j in (queued, running, done)] == ["interrupted", "interrupted", "succeeded"]
