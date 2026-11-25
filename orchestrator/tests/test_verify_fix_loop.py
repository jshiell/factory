import json
from dataclasses import replace

import pytest

from factory.command import RunnerConfig
from factory.ledger import Ledger
from factory.step import BudgetCapReached, RunResult
from factory.store import TaskStore
from factory.task import Task, TaskState
from factory.verify_task import verify_task
from factory.worktrees import WorktreeManager
from gitutil import git
from planted_gitdir import redirect_to_planted_gitdir

CONFIG = RunnerConfig(model="sonnet", budget_usd=0.3)
FIXED_ONLY_IF_FILE = ["sh", "-c", "echo 'FAIL: no fixed file'; test -f fixed"]


def success(cost=0.1):
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": cost})


class FakeRunner:
    """Each call pops the next action: a callable run before answering, or an exception to raise."""

    def __init__(self, repo, *actions, stdout=None, timed_out=False):
        self.repo, self.actions, self.calls = repo, list(actions), []
        self.stdout, self.timed_out = stdout or success(), timed_out

    def run(self, task, config):
        self.calls.append((task, config))
        action = self.actions.pop(0) if self.actions else None
        if isinstance(action, BaseException):
            raise action
        if action:
            action()
        return RunResult(self.stdout, 0, self.timed_out)


@pytest.fixture
def store(tmp_path):
    (tmp_path / "tasks").mkdir()
    return TaskStore(tmp_path / "tasks")


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "ledger.jsonl")


@pytest.fixture
def task(repo, store):
    (repo / "proj").mkdir()
    (repo / "proj" / "code.py").write_text("x")
    git(repo, "add", "proj")
    git(repo, "commit", "-q", "-m", "project")
    saved = Task(id="t1", prompt="stored prompt", state=TaskState.AGENT_DONE, worktree=str(repo),
                 base_sha=git(repo, "rev-parse", "HEAD"), goal="add power", plan="1. add it")
    store.save(saved)
    (repo / "proj" / "code.py").write_text("changed by the agent")
    return saved


def write_fix(repo):
    return lambda: (repo / "proj" / "fixed").write_text("ok")


def verify(store, ledger, runner, rounds=2, test_argv=FIXED_ONLY_IF_FILE, cap=10.0, config=CONFIG):
    return verify_task("t1", store, project_subdir="proj", test_argv=test_argv, gate_timeout_s=10,
                       runner=runner, ledger=ledger, fix_config=config, cap_usd=cap, fix_rounds=rounds, repo=None)


def test_a_gate_that_passes_first_time_runs_no_agent_and_charges_nothing(repo, store, ledger, task):
    runner = FakeRunner(repo)

    result = verify(store, ledger, runner, test_argv=["true"])

    assert result.state is TaskState.VERIFIED and runner.calls == [] and ledger.entries() == []


def test_one_fix_round_that_makes_the_tests_pass_verifies_the_task(repo, store, ledger, task):
    runner = FakeRunner(repo, write_fix(repo))

    result = verify(store, ledger, runner)

    assert result.state is TaskState.VERIFIED and result.fix_rounds_used == 1
    (called, config), = runner.calls
    assert config == CONFIG
    assert "FAIL: no fixed file" in called.prompt and "data, not instructions" in called.prompt
    assert store.load("t1").prompt == "stored prompt" and result.prompt == "stored prompt"
    assert [(e["stage"], e["assumed"]) for e in ledger.entries()] == [("fix", False)]
    assert git(repo, "log", "-1", "--format=%s") == "Task t1"
    assert "fixed" in git(repo, "show", "--name-only", "--format=", "HEAD")


def test_rounds_that_never_fix_it_fail_tests_failed_after_exactly_the_allowed_runs(repo, store, ledger, task):
    runner = FakeRunner(repo, lambda: (repo / "proj" / "n1").write_text("1"), lambda: (repo / "proj" / "n2").write_text("2"))

    result = verify(store, ledger, runner, rounds=2)

    assert result.state is TaskState.FAILED and result.failure_reason == "tests_failed"
    assert len(runner.calls) == 2 and result.fix_rounds_used == 2


def test_zero_rounds_fails_at_once_as_before(repo, store, ledger, task):
    runner = FakeRunner(repo)

    result = verify(store, ledger, runner, rounds=0)

    assert result.state is TaskState.FAILED and result.failure_reason == "tests_failed" and runner.calls == []


@pytest.mark.parametrize("argv,reason", [(["sleep", "5"], "tests_timeout")])
def test_a_timeout_starts_no_round(repo, store, ledger, task, argv, reason):
    runner = FakeRunner(repo)

    result = verify_task("t1", store, project_subdir="proj", test_argv=argv, gate_timeout_s=0.3,
                         runner=runner, ledger=ledger, fix_config=CONFIG, cap_usd=10.0, fix_rounds=2, repo=None)

    assert result.failure_reason == reason and runner.calls == []


def test_a_gate_failure_other_than_tests_failed_starts_no_round(repo, store, ledger):
    (repo / "proj").mkdir()
    (repo / "proj" / "code.py").write_text("x")
    git(repo, "add", "proj")
    git(repo, "commit", "-q", "-m", "project")
    store.save(Task(id="t1", prompt="p", state=TaskState.AGENT_DONE, worktree=str(repo),
                    base_sha=git(repo, "rev-parse", "HEAD")))
    runner = FakeRunner(repo)

    result = verify(store, ledger, runner)  # nothing changed: no_commits

    assert result.failure_reason == "no_commits" and runner.calls == []


def test_a_round_the_cap_cannot_afford_raises_leaving_the_task_untouched(repo, store, ledger, task):
    ledger.append("other", 9.9)
    runner = FakeRunner(repo)

    with pytest.raises(BudgetCapReached):
        verify(store, ledger, runner, cap=10.0)

    saved = store.load("t1")
    assert saved.state is TaskState.AGENT_DONE and saved.fix_rounds_used == 0 and runner.calls == []


def test_a_runner_exception_charges_the_assumed_cost_and_fails_fix_runner_error(repo, store, ledger, task):
    result = verify(store, ledger, FakeRunner(repo, RuntimeError("boom")))

    assert (result.state, result.failure_reason) == (TaskState.FAILED, "fix_runner_error")
    assert [(e["stage"], e["assumed"], e["cost_usd"]) for e in ledger.entries()] == [("fix", True, 0.6)]


def test_a_runner_timeout_fails_fix_timeout(repo, store, ledger, task):
    result = verify(store, ledger, FakeRunner(repo, timed_out=True))

    assert result.failure_reason == "fix_timeout"
    assert ledger.entries()[0]["assumed"] is True


def test_a_non_success_agent_outcome_fails_with_its_kind(repo, store, ledger, task):
    errored = json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True})

    result = verify(store, ledger, FakeRunner(repo, stdout=errored))

    assert result.failure_reason == "fix_error"


def test_an_agent_that_moves_head_fails_fix_moved(repo, store, ledger, task):
    def commit():
        (repo / "proj" / "fixed").write_text("ok")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "agent commit")

    result = verify(store, ledger, FakeRunner(repo, commit))

    assert result.failure_reason == "fix_moved"


def test_a_nested_repo_created_in_a_round_fails_before_anything_is_committed(repo, store, ledger, task):
    def plant():
        (repo / "proj" / "x" / ".git").mkdir(parents=True)
        (repo / "proj" / "x" / "file").write_text("f")

    runner = FakeRunner(repo, plant)
    verify(store, ledger, FakeRunner(repo), rounds=0)  # first pass: committed, tests fail
    store.save(task)
    head = git(repo, "rev-parse", "HEAD")

    result = verify(store, ledger, runner)

    assert result.failure_reason == "nested_repo"
    assert git(repo, "rev-parse", "HEAD") == head


def test_a_round_that_deletes_a_line_of_an_existing_test_fails_tests_modified(repo, store, ledger):
    (repo / "proj").mkdir()
    (repo / "proj" / "test_old.py").write_text("def test_a():\n    pass\n\n\ndef test_b():\n    pass\n")
    git(repo, "add", "proj")
    git(repo, "commit", "-q", "-m", "project")
    store.save(Task(id="t1", prompt="p", state=TaskState.AGENT_DONE, worktree=str(repo),
                    base_sha=git(repo, "rev-parse", "HEAD")))
    (repo / "proj" / "test_old.py").write_text("def test_a():\n    pass\n\n\ndef test_b():\n    pass\n\n\ndef test_c():\n    pass\n")
    def weaken():
        (repo / "proj" / "test_old.py").write_text("def test_a():\n    pass\n")

    result = verify(store, ledger, FakeRunner(repo, weaken))

    assert result.failure_reason == "tests_modified"


def test_a_round_that_changes_nothing_fails_fix_no_changes_without_burning_more_rounds(repo, store, ledger, task):
    runner = FakeRunner(repo)

    result = verify(store, ledger, runner, rounds=2)

    assert result.failure_reason == "fix_no_changes" and len(runner.calls) == 1


def test_files_a_failing_gate_generated_do_not_count_as_a_fix_round_change(repo, store, ledger, task):
    runner = FakeRunner(repo)

    result = verify(store, ledger, runner, rounds=2, test_argv=["sh", "-c", "touch proj/uv.lock; exit 1"])

    assert result.failure_reason == "fix_no_changes" and len(runner.calls) == 1
    assert git(repo, "status", "--porcelain") == ""


def test_the_counter_and_marker_are_saved_before_the_agent_runs(repo, store, ledger, task):
    seen = []
    runner = FakeRunner(repo, lambda: seen.append(store.load("t1")), write_fix(repo))

    result = verify(store, ledger, runner)

    assert (seen[0].fix_rounds_used, seen[0].fix_in_flight) == (1, True)
    assert result.fix_in_flight is False and store.load("t1").fix_in_flight is False


def test_a_resumed_task_only_gets_the_remaining_rounds(repo, store, ledger, task):
    store.save(Task(**{**task.__dict__, "fix_rounds_used": 1}))
    runner = FakeRunner(repo, lambda: (repo / "proj" / "n").write_text("1"), lambda: (repo / "proj" / "m").write_text("2"))

    result = verify(store, ledger, runner, rounds=2)

    assert len(runner.calls) == 1 and result.failure_reason == "tests_failed" and result.fix_rounds_used == 2


def test_a_task_left_with_the_marker_set_fails_fix_interrupted_touching_no_git(repo, store, ledger, task):
    store.save(Task(**{**task.__dict__, "fix_rounds_used": 1, "fix_in_flight": True}))
    head = git(repo, "rev-parse", "HEAD")
    runner = FakeRunner(repo)

    result = verify(store, ledger, runner)

    assert (result.state, result.failure_reason, result.fix_in_flight) == (TaskState.FAILED, "fix_interrupted", False)
    assert runner.calls == [] and git(repo, "rev-parse", "HEAD") == head
    assert git(repo, "status", "--porcelain") != ""  # the agent's possibly-live edits stay uncommitted
    assert [(e["stage"], e["assumed"], e["cost_usd"]) for e in ledger.entries()] == [("fix", True, 0.6)]


def test_the_ledger_and_state_are_written_before_any_later_git_call_can_fail(repo, store, ledger, task):
    import shutil
    runner = FakeRunner(repo, lambda: shutil.rmtree(repo / ".git"))

    with pytest.raises(Exception):  # noqa: B017  any later git failure will do, whichever call fails first
        verify(store, ledger, runner)

    assert [e["stage"] for e in ledger.entries()] == ["fix"]
    assert store.load("t1").fix_in_flight is False


def test_the_pre_round_head_is_saved_on_the_task_before_the_agent_runs(repo, store, ledger, task):
    seen = []
    runner = FakeRunner(repo, lambda: seen.append(store.load("t1")), write_fix(repo))

    verify(store, ledger, runner)

    # the implementer's work is committed by the first gate run, so the pre-round HEAD is that commit
    assert seen[0].fix_frozen_sha == git(repo, "rev-list", "--max-count=1", "--grep=Task t1", "HEAD")


def implementer_test(repo):
    (repo / "proj" / "test_new.py").write_text("def test_n():\n    assert False\n")


def test_a_round_that_rewrites_a_test_the_implementer_added_fails_tests_modified(repo, store, ledger, task):
    implementer_test(repo)
    def rewrite():
        (repo / "proj" / "test_new.py").write_text("def test_n():\n    assert True\n")
        write_fix(repo)()
    runner = FakeRunner(repo, rewrite)

    result = verify(store, ledger, runner)

    assert (result.state, result.failure_reason, result.fix_rounds_used) == (TaskState.FAILED, "tests_modified", 1)
    assert len(runner.calls) == 1


def test_a_round_that_deletes_a_test_the_implementer_added_fails_tests_modified(repo, store, ledger, task):
    implementer_test(repo)
    runner = FakeRunner(repo, lambda: ((repo / "proj" / "test_new.py").unlink(), write_fix(repo)()))

    result = verify(store, ledger, runner)

    assert (result.state, result.failure_reason) == (TaskState.FAILED, "tests_modified")


def test_a_round_that_only_adds_a_new_test_file_still_reaches_the_gate_result(repo, store, ledger, task):
    implementer_test(repo)
    def add():
        (repo / "proj" / "test_extra.py").write_text("def test_e():\n    pass\n")
        write_fix(repo)()
    runner = FakeRunner(repo, add)

    result = verify(store, ledger, runner, test_argv=["sh", "-c", "test -f fixed"])

    assert result.state == TaskState.VERIFIED


def test_a_resumed_task_is_still_frozen_against_its_stored_pre_round_head(repo, store, ledger, task):
    implementer_test(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Task t1")
    frozen = git(repo, "rev-parse", "HEAD")
    store.save(Task(**{**task.__dict__, "fix_rounds_used": 1, "fix_frozen_sha": frozen}))
    (repo / "proj" / "test_new.py").write_text("def test_n():\n    assert True\n")  # the round's edit, uncommitted
    runner = FakeRunner(repo)

    result = verify(store, ledger, runner, test_argv=["true"])

    assert (result.state, result.failure_reason) == (TaskState.FAILED, "tests_modified")
    assert runner.calls == []


def plant_ignored_files(repo):
    (repo / "proj" / ".gitignore").write_text("conftest.py\n")
    (repo / "proj" / "conftest.py").write_text("planted")
    (repo / "proj" / ".venv").mkdir()
    (repo / "proj" / ".venv" / ".gitignore").write_text("*\n")
    (repo / "proj" / ".venv" / "x.pth").write_text("import os")


def test_the_gate_runs_only_committed_files_and_cleans_before_the_sandbox_is_built(repo, store, ledger, task):
    import os

    from factory.gate_sandbox import GateSandbox
    plant_ignored_files(repo)
    seen = []

    def sandbox(_task):
        seen.append((repo / "proj" / "conftest.py").exists())
        return GateSandbox((), ("sh", "-c", "test ! -e conftest.py && test ! -e .venv/x.pth"), {"PATH": os.environ["PATH"]})

    result = verify_task("t1", store, project_subdir="proj", test_argv=["unused"], gate_timeout_s=10, sandbox=sandbox, repo=None)

    assert result.state == TaskState.VERIFIED
    assert seen == [False]


def test_a_gate_that_cannot_clean_fails_the_task_instead_of_raising(repo, store, ledger, task):
    state_inside = repo / ".factory"
    state_inside.mkdir()
    (state_inside / "ledger.jsonl").write_text("l")

    result = verify_task("t1", store, project_subdir="proj", test_argv=["true"], gate_timeout_s=10, factory_paths=(state_inside,), repo=None)

    assert (result.state, result.failure_reason) == (TaskState.FAILED, "worktree_mismatch")
    assert (state_inside / "ledger.jsonl").exists()


def test_a_fix_round_that_plants_an_ignored_file_alongside_a_real_change_is_cleaned_before_the_next_gate(repo, store, ledger, task):
    def plant_and_fix():
        plant_ignored_files(repo)
        write_fix(repo)()

    runner = FakeRunner(repo, plant_and_fix)

    result = verify(store, ledger, runner, test_argv=["sh", "-c", "test -f fixed && test ! -e conftest.py && test ! -e .venv/x.pth"])

    assert result.state == TaskState.VERIFIED


def test_a_change_outside_the_project_verifies_the_task_without_a_fix_round(repo, store, ledger, task, tmp_path):
    (repo / "README.md").write_text("edited by the agent")
    sentinel = tmp_path / "tests-ran"
    runner = FakeRunner(repo)

    result = verify(store, ledger, runner, rounds=1, test_argv=["touch", str(sentinel)])

    assert result.state is TaskState.VERIFIED
    assert runner.calls == [] and result.fix_rounds_used == 0 and sentinel.exists()
    assert ledger.entries() == []


def test_an_allowed_root_change_beside_the_project_change_verifies_the_task(repo, store, ledger, task):
    (repo / "README.md").write_text("edited by the agent")

    result = verify_task("t1", store, project_subdir="proj", test_argv=["true"], gate_timeout_s=10,
                         repo=None)

    assert result.state is TaskState.VERIFIED


@pytest.mark.parametrize("allowed", [False, True])
def test_the_fix_prompt_follows_the_dependency_setting(repo, store, ledger, task, allowed):
    runner = FakeRunner(repo, write_fix(repo))

    verify_task("t1", store, project_subdir="proj", test_argv=FIXED_ONLY_IF_FILE, gate_timeout_s=10,
                runner=runner, ledger=ledger, fix_config=CONFIG, cap_usd=10.0, fix_rounds=1,
                gate_allow_dependency_changes=allowed, repo=None)

    (called, _), = runner.calls
    assert ("Do not add or change dependencies" in called.prompt) is not allowed


def test_an_allowed_root_change_passes_the_sandboxed_gate_too(repo, store, ledger, task):
    import os

    from factory.gate_sandbox import GateSandbox
    (repo / "README.md").write_text("edited by the agent")
    def sandbox(_task):
        return GateSandbox((), ("true",), {"PATH": os.environ["PATH"]})

    result = verify_task("t1", store, project_subdir="proj", test_argv=["unused"], gate_timeout_s=10,
                         sandbox=sandbox, repo=None)

    assert result.state is TaskState.VERIFIED


def add_a_dependency(repo, store):
    """The base already has a pyproject; the agent then adds a dependency to it."""
    pyproject = repo / "proj" / "pyproject.toml"
    pyproject.write_text('[project]\nname = "p"\n')
    stash = (repo / "proj" / "code.py").read_text()
    (repo / "proj" / "code.py").write_text("x")
    git(repo, "add", "proj")
    git(repo, "commit", "-q", "-m", "base pyproject")
    store.save(replace(store.load("t1"), base_sha=git(repo, "rev-parse", "HEAD")))
    (repo / "proj" / "code.py").write_text(stash)
    pyproject.write_text('[project]\nname = "p"\ndependencies = ["requests"]\n')


def test_a_dependency_change_fails_protected_file_changed_by_default(repo, store, ledger, task):
    add_a_dependency(repo, store)

    result = verify_task("t1", store, project_subdir="proj", test_argv=["true"], gate_timeout_s=10, repo=None)

    assert result.state is TaskState.FAILED and result.failure_reason == "protected_file_changed"


def test_a_dependency_change_verifies_when_allowed(repo, store, ledger, task):
    add_a_dependency(repo, store)

    result = verify_task("t1", store, project_subdir="proj", test_argv=["true"], gate_timeout_s=10,
                         gate_allow_dependency_changes=True, repo=None)

    assert result.state is TaskState.VERIFIED


def test_a_dependency_change_fails_the_sandboxed_gate_by_default_and_passes_when_allowed(repo, store, ledger, task):
    import os

    from factory.gate_sandbox import GateSandbox
    add_a_dependency(repo, store)
    def sandbox(_task):
        return GateSandbox((), ("true",), {"PATH": os.environ["PATH"]})

    refused = verify_task("t1", store, project_subdir="proj", test_argv=["unused"], gate_timeout_s=10, sandbox=sandbox, repo=None)
    store.save(replace(store.load("t1"), state=TaskState.AGENT_DONE, failure_reason=None))
    allowed = verify_task("t1", store, project_subdir="proj", test_argv=["unused"], gate_timeout_s=10,
                          sandbox=sandbox, gate_allow_dependency_changes=True, repo=None)

    assert refused.failure_reason == "protected_file_changed" and allowed.state is TaskState.VERIFIED


def test_a_uv_toml_beside_the_change_fails_protected_file_changed_without_a_fix_round(repo, store, ledger, task, tmp_path):
    (repo / "proj" / "uv.toml").write_text("exclude-newer = '2000-01-01T00:00:00Z'\n")
    sentinel = tmp_path / "tests-ran"
    runner = FakeRunner(repo)

    result = verify(store, ledger, runner, rounds=1, test_argv=["touch", str(sentinel)])

    assert result.state is TaskState.FAILED and result.failure_reason == "protected_file_changed"
    assert runner.calls == [] and result.fix_rounds_used == 0 and not sentinel.exists()


def test_a_fix_round_that_edits_outside_the_project_is_gated_by_the_tests(repo, store, ledger, task):
    runner = FakeRunner(repo, lambda: (repo / "README.md").write_text("edited by the fixer"))

    result = verify(store, ledger, runner, rounds=2)

    assert result.state is TaskState.FAILED and result.failure_reason == "fix_no_changes"
    assert len(runner.calls) == 2 and result.fix_rounds_used == 2


def test_a_fixer_that_rewrites_dot_git_fails_the_gate_without_running_the_planted_repos_filter(repo, store, ledger, tmp_path):
    (repo / "proj").mkdir()
    (repo / "proj" / "code.py").write_text("x")
    git(repo, "add", "proj")
    git(repo, "commit", "-q", "-m", "project")
    worktree, base = WorktreeManager(repo, tmp_path / "wt").create("t1")
    store.save(Task(id="t1", prompt="p", state=TaskState.AGENT_DONE, worktree=str(worktree), base_sha=base))
    (worktree / "proj" / "code.py").write_text("changed by the agent")
    markers = []

    def fix_and_redirect():
        (worktree / "proj" / "fixed").write_text("ok")
        markers.append(redirect_to_planted_gitdir(worktree, tmp_path, clone_of=repo))

    result = verify_task("t1", store, project_subdir="proj", test_argv=FIXED_ONLY_IF_FILE, gate_timeout_s=10,
                         runner=FakeRunner(repo, fix_and_redirect), ledger=ledger, fix_config=CONFIG, cap_usd=10.0,
                         fix_rounds=1, repo=repo)

    assert (result.state, result.failure_reason) == (TaskState.FAILED, "worktree_mismatch")
    assert not markers[0].exists()


def test_tests_that_rewrite_dot_git_fail_worktree_mismatch_before_a_fixer_runs(repo, store, ledger, tmp_path):
    (repo / "proj").mkdir()
    (repo / "proj" / "code.py").write_text("x")
    git(repo, "add", "proj")
    git(repo, "commit", "-q", "-m", "project")
    worktree, base = WorktreeManager(repo, tmp_path / "wt").create("t1")
    store.save(Task(id="t1", prompt="p", state=TaskState.AGENT_DONE, worktree=str(worktree), base_sha=base))
    (worktree / "proj" / "code.py").write_text("changed by the agent")
    runner = FakeRunner(repo)

    result = verify_task("t1", store, project_subdir="proj", gate_timeout_s=10,
                         test_argv=["sh", "-c", f"echo 'gitdir: {tmp_path}' > {worktree}/.git; exit 1"],
                         runner=runner, ledger=ledger, fix_config=CONFIG, cap_usd=10.0, fix_rounds=1, repo=repo)

    assert (result.state, result.failure_reason) == (TaskState.FAILED, "worktree_mismatch")
    assert runner.calls == [] and result.fix_rounds_used == 0
