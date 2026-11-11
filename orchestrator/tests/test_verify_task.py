import pytest

from gitutil import git

from factory.process_registry import load_records
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


@pytest.mark.parametrize("form", ["directory", "gitfile"])
def test_a_nested_repo_under_the_project_fails_the_gate_before_anything_is_committed(repo, store, form):
    store.save(agent_done_task(repo))
    head = git(repo, "rev-parse", "HEAD")
    nested = repo / "proj" / "deep" / "x"
    nested.mkdir(parents=True)
    if form == "directory":
        (nested / ".git").mkdir()
    else:
        (nested / ".git").write_text("gitdir: /tmp/elsewhere\n")

    task = verify(store)

    assert (task.state, task.failure_reason) == (TaskState.FAILED, "nested_repo")
    assert git(repo, "rev-parse", "HEAD") == head


def test_the_gate_runs_its_tests_through_the_sandbox_when_one_is_given(repo, store, tmp_path):
    from factory.gate_sandbox import GateSandbox
    marker = tmp_path / "ran-in-sandbox"
    store.save(agent_done_task(repo))
    (repo / "proj" / "code.py").write_text("changed by agent")
    sandbox = lambda task: GateSandbox(  # noqa: E731
        prefix=("sh", "-c", f'touch {marker}; exec "$@"', "sandbox"), argv=("true",), env={"PATH": "/usr/bin:/bin"})

    task = verify_task("t1", store, project_subdir="proj", test_argv=["false"], gate_timeout_s=10, sandbox=sandbox)

    assert task.state is TaskState.VERIFIED and marker.exists()


def test_a_root_pytest_config_written_by_the_agent_fails_tests_modified_without_running_the_tests(repo, store, tmp_path):
    store.save(agent_done_task(repo))
    (repo / "proj" / "code.py").write_text("changed by agent")
    (repo / "pytest.ini").write_text("[pytest]\naddopts = --collect-only\n")
    ran = tmp_path / "ran"

    task = verify_task(
        "t1", store, project_subdir="proj",
        test_argv=["touch", str(ran)], gate_timeout_s=10,
    )

    assert (task.state, task.failure_reason) == (TaskState.FAILED, "tests_modified")
    assert not ran.exists()


def test_the_gate_test_run_is_recorded_as_a_gate_process_while_it_runs_and_the_record_goes_after(repo, store, tmp_path):
    state, seen = tmp_path / "state", tmp_path / "seen.json"
    store.save(agent_done_task(repo))
    (repo / "proj" / "code.py").write_text("changed by agent")

    task = verify_task("t1", store, project_subdir="proj", gate_timeout_s=10, records=state,
                       test_argv=["sh", "-c", 'cat "$1"/*.json > "$2"', "_", str(state / "procs"), str(seen)])

    assert task.state is TaskState.VERIFIED
    assert '"task_id": "t1"' in seen.read_text() and '"stage": "gate"' in seen.read_text()
    assert load_records(state) == []
