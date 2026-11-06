import pytest

from gitutil import git

from factory.store import TaskStore
from factory.task import InvalidTransition, Task, TaskState
from factory.verify_task import verify_task


@pytest.fixture
def store(tmp_path):
    (tmp_path / "tasks").mkdir()
    return TaskStore(tmp_path / "tasks")


def agent_done_task(repo, state=TaskState.AGENT_DONE) -> Task:
    (repo / "proj").mkdir()
    (repo / "proj" / "code.py").write_text("x")
    git(repo, "add", "proj")
    git(repo, "commit", "-q", "-m", "project")
    return Task(
        id="t1", prompt="p", state=state, worktree=str(repo), base_sha=git(repo, "rev-parse", "HEAD")
    )


def verify(store, passing=True):
    return verify_task(
        "t1", store, project_subdir="proj",
        test_argv=["true"] if passing else ["false"], gate_timeout_s=10,
    )


def test_agent_changes_are_committed_and_a_passing_gate_marks_the_task_verified(repo, store):
    store.save(agent_done_task(repo))
    (repo / "proj" / "code.py").write_text("changed by agent")

    task = verify(store)

    assert task.state is TaskState.VERIFIED
    assert store.load("t1") == task
    assert git(repo, "log", "-1", "--format=%s") == "Task t1"


def test_a_failing_gate_marks_the_task_failed_with_the_gate_reason(repo, store):
    store.save(agent_done_task(repo))

    task = verify(store)

    assert task.state is TaskState.FAILED
    assert task.failure_reason == "no_commits"
    assert store.load("t1") == task


def test_task_that_is_not_agent_done_is_rejected_without_committing(repo, store):
    store.save(agent_done_task(repo, state=TaskState.RUNNING))
    (repo / "proj" / "code.py").write_text("uncommitted")

    with pytest.raises(InvalidTransition):
        verify(store)

    assert git(repo, "status", "--porcelain") != ""
