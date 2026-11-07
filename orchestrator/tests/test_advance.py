import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from factory.advance import ACTIONS, Step, advance
from factory.gate import ProjectSubdirEscapes
from factory.run_task import WorktreeNotPristine
from factory.step import BudgetCapReached, MissingBudget
from factory.store import TaskStore
from factory.task import InvalidTransition, Task, TaskState
from factory.worktrees import BranchExists, WorktreeExists

S = TaskState


@pytest.fixture
def store(tmp_path):
    (tmp_path / "tasks").mkdir()
    return TaskStore(tmp_path / "tasks")


def start(store, state):
    store.save(Task(id="t1", prompt="", state=state))


def moving(store, name, to, log):
    def stage(task_id):
        log.append(name)
        task = replace(store.load(task_id), state=to)
        store.save(task)
        return task
    return stage


def pipeline(store, log):
    return {
        "plan": moving(store, "plan", S.PLANNED, log),
        "review": moving(store, "review", S.REVIEWED, log),
        "implement": moving(store, "implement", S.AGENT_DONE, log),
        "verify": moving(store, "verify", S.VERIFIED, log),
        "audit": moving(store, "audit", S.AUDITED, log),
    }


def test_the_action_table_maps_states_to_stages():
    assert ACTIONS == {S.PROPOSED: "plan", S.PLANNED: "review", S.QUEUED: "implement",
                       S.AGENT_DONE: "verify", S.VERIFIED: "audit"}


def test_a_proposed_task_runs_plan_then_review_and_stops_at_reviewed_for_a_human(store):
    start(store, S.PROPOSED)
    log = []

    result = advance("t1", store, pipeline(store, log))

    assert log == ["plan", "review"]
    assert result.steps == [Step("plan", S.PLANNED), Step("review", S.REVIEWED)]
    assert result.task.state is S.REVIEWED and result.stopped is None


def test_a_queued_task_runs_implement_verify_audit_to_audited(store):
    start(store, S.QUEUED)
    log = []

    result = advance("t1", store, pipeline(store, log))

    assert log == ["implement", "verify", "audit"] and result.task.state is S.AUDITED


@pytest.mark.parametrize("state", [S.REVIEWED, S.AUDITED, S.FAILED])
def test_stop_states_run_nothing(store, state):
    start(store, state)
    log = []

    result = advance("t1", store, pipeline(store, log))

    assert log == [] and result.steps == [] and result.task.state is state


def test_a_running_task_is_reported_stale_and_left_alone(store):
    start(store, S.RUNNING)
    log = []

    result = advance("t1", store, pipeline(store, log))

    assert log == [] and "agent may still be alive" in result.stopped


def test_a_stage_that_leaves_the_state_unchanged_stops_the_loop_with_the_recorded_error(store):
    start(store, S.PROPOSED)

    def stuck(task_id):
        task = replace(store.load(task_id), plan_error="timeout")
        store.save(task)
        return task

    result = advance("t1", store, {**pipeline(store, []), "plan": stuck})

    assert result.stopped == "plan: no progress (timeout)"
    assert result.steps == []


def test_a_failed_stage_is_a_step_and_ends_the_loop(store):
    start(store, S.QUEUED)
    failing = moving(store, "implement", S.FAILED, [])

    result = advance("t1", store, {**pipeline(store, []), "implement": failing})

    assert result.steps == [Step("implement", S.FAILED)] and result.task.state is S.FAILED


def test_the_step_cap_stops_a_table_that_never_terminates(store):
    start(store, S.PROPOSED)
    cycle = {**pipeline(store, []), "review": moving(store, "review", S.PROPOSED, [])}

    result = advance("t1", store, cycle, max_steps=5)

    assert len(result.steps) == 5 and "step cap" in result.stopped


@pytest.mark.parametrize(
    "error,text",
    [
        (BudgetCapReached("t1"), "BudgetCapReached"),
        (MissingBudget("t1"), "MissingBudget"),
        (InvalidTransition("nope"), "InvalidTransition"),
        (WorktreeExists("t1"), "WorktreeExists"),
        (BranchExists("factory/t1"), "BranchExists"),
        (subprocess.CalledProcessError(128, ["git"]), "CalledProcessError"),
        (ProjectSubdirEscapes("../x"), "ProjectSubdirEscapes"),
    ],
)
def test_known_stage_exceptions_are_reported_with_the_stage_and_the_exception(store, error, text):
    start(store, S.PROPOSED)

    def boom(task_id):
        raise error

    result = advance("t1", store, {**pipeline(store, []), "plan": boom})

    assert result.stopped.startswith("plan: ") and text in result.stopped
    assert result.task.state is S.PROPOSED


def test_a_dirty_pre_made_worktree_is_reported_as_needing_human_action(store):
    start(store, S.QUEUED)

    def dirty(task_id):
        raise WorktreeNotPristine(task_id)

    result = advance("t1", store, {**pipeline(store, []), "implement": dirty})

    assert "needs human action" in result.stopped


def test_stale_error_fields_are_not_reported_when_an_exception_is_the_cause(store):
    store.save(Task(id="t1", prompt="", state=S.PROPOSED, plan_error="timeout"))

    def boom(task_id):
        raise BudgetCapReached(task_id)

    result = advance("t1", store, {**pipeline(store, []), "plan": boom})

    assert "timeout" not in result.stopped


def test_unknown_exceptions_propagate(store):
    start(store, S.PROPOSED)

    def boom(task_id):
        raise RuntimeError("bug")

    with pytest.raises(RuntimeError):
        advance("t1", store, {**pipeline(store, []), "plan": boom})


def test_the_driver_never_imports_the_approval_functions():
    source = (Path(__file__).parent.parent / "src" / "factory" / "advance.py").read_text()

    assert "approve_plan" not in source and "reject_plan" not in source
