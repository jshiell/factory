import pytest

from factory.process_registry import load_records
from factory.store import TaskStore
from factory.task import InvalidTransition, Task, TaskState
from factory.verify_task import verify_task
from factory.worktrees import WorktreeManager
from gitutil import git
from planted_gitdir import redirect_to_planted_gitdir
from slowrecords import RecordsWrittenLate


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
        test_argv=["true"] if passing else ["false"], gate_timeout_s=10, repo=None,
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

    task = verify_task("t1", store, project_subdir="proj", test_argv=["false"], gate_timeout_s=10, sandbox=sandbox, repo=None)

    assert task.state is TaskState.VERIFIED and marker.exists()


def test_a_passing_gate_leaves_no_files_its_tests_generated(repo, store):
    store.save(agent_done_task(repo))
    (repo / "proj" / "code.py").write_text("changed by agent")

    task = verify_task("t1", store, project_subdir="proj", test_argv=["touch", "uv.lock"], gate_timeout_s=10, repo=None)

    assert task.state is TaskState.VERIFIED
    assert git(repo, "status", "--porcelain") == ""


def test_a_root_pytest_config_written_by_the_agent_fails_protected_file_changed_without_running_the_tests(repo, store, tmp_path):
    store.save(agent_done_task(repo))
    (repo / "proj" / "code.py").write_text("changed by agent")
    (repo / "pytest.ini").write_text("[pytest]\naddopts = --collect-only\n")
    ran = tmp_path / "ran"

    task = verify_task(
        "t1", store, project_subdir="proj",
        test_argv=["touch", str(ran)], gate_timeout_s=10, repo=None,
    )

    assert (task.state, task.failure_reason) == (TaskState.FAILED, "protected_file_changed")
    assert not ran.exists()


def test_the_gate_test_run_is_recorded_as_a_gate_process_while_it_runs_and_the_record_goes_after(repo, store, tmp_path):
    state, seen = tmp_path / "state", tmp_path / "seen.json"
    store.save(agent_done_task(repo))
    (repo / "proj" / "code.py").write_text("changed by agent")

    task = verify_task("t1", store, project_subdir="proj", gate_timeout_s=10, records=RecordsWrittenLate(state),
                       test_argv=["sh", "-c", 'for _ in $(seq 100); do ls "$1"/*.json >/dev/null 2>&1 && break; sleep 0.05; done; cat "$1"/*.json > "$2"', "_", str(state / "procs"), str(seen)], repo=None)

    assert task.state is TaskState.VERIFIED
    assert '"task_id": "t1"' in seen.read_text() and '"stage": "gate"' in seen.read_text()
    assert load_records(state) == []


def test_tests_that_rewrite_dot_git_fail_the_gate_without_running_the_planted_repos_filter_in_the_cleanup(repo, store, tmp_path):
    (repo / "proj").mkdir()
    (repo / "proj" / "code.py").write_text("x")
    git(repo, "add", "proj")
    git(repo, "commit", "-q", "-m", "project")
    worktree, base = WorktreeManager(repo, tmp_path / "wt").create("t1")
    store.save(Task(id="t1", prompt="p", state=TaskState.AGENT_DONE, worktree=str(worktree), base_sha=base))
    (worktree / "proj" / "code.py").write_text("changed by agent")
    real_pointer = (worktree / ".git").read_text()
    marker = redirect_to_planted_gitdir(worktree, tmp_path, clone_of=repo)
    planted_pointer = (worktree / ".git").read_text()
    (worktree / ".git").write_text(real_pointer)
    rewrite = ["sh", "-c", f"printf '%s' '{planted_pointer}' > {worktree}/.git"]

    task = verify_task("t1", store, project_subdir="proj", test_argv=rewrite, gate_timeout_s=10, repo=repo)

    assert (task.state, task.failure_reason) == (TaskState.FAILED, "worktree_mismatch")
    assert not marker.exists()


def test_a_rewritten_dot_git_file_fails_the_gate_without_running_the_planted_repos_filter(repo, store, tmp_path):
    (repo / "proj").mkdir()
    (repo / "proj" / "code.py").write_text("x")
    git(repo, "add", "proj")
    git(repo, "commit", "-q", "-m", "project")
    worktree, base = WorktreeManager(repo, tmp_path / "wt").create("t1")
    store.save(Task(id="t1", prompt="p", state=TaskState.AGENT_DONE, worktree=str(worktree), base_sha=base))
    (worktree / "proj" / "code.py").write_text("changed by agent")
    marker = redirect_to_planted_gitdir(worktree, tmp_path, clone_of=repo)

    task = verify_task("t1", store, project_subdir="proj", test_argv=["true"], gate_timeout_s=10, repo=repo)

    assert (task.state, task.failure_reason) == (TaskState.FAILED, "worktree_mismatch")
    assert not marker.exists()
