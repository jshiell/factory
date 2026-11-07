import io
from dataclasses import replace
from datetime import datetime

import pytest

from factory import cli
from factory.cli import IO, Context, escape_control, main
from factory.digest import plan_digest
from factory.ledger import Ledger
from factory.settings import Settings
from factory.store import TaskStore
from factory.task import Task, TaskState

S = TaskState


class FakeStdin(io.StringIO):
    def __init__(self, text="", tty=True):
        super().__init__(text)
        self._tty = tty

    def isatty(self):
        return self._tty


@pytest.fixture
def ctx(tmp_path):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt")
    store = TaskStore(settings.tasks_dir)
    return Context(settings, store, Ledger(settings.ledger_path), stages={}, clock=lambda: datetime(2026, 1, 2, 3, 4, 5))


def run(ctx, *argv, stdin="", tty=True):
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), IO(FakeStdin(stdin, tty), out, err), ctx)
    return code, out.getvalue(), err.getvalue()


def put(ctx, state=S.PROPOSED, task_id="t1", **fields):
    ctx.settings.tasks_dir.mkdir(parents=True, exist_ok=True)
    ctx.store.save(Task(id=task_id, prompt="", state=state, **fields))


# --- new / list / show -------------------------------------------------------

def test_new_creates_a_proposed_task_with_the_goal_and_prints_its_id(ctx):
    code, out, _ = run(ctx, "new", "add multiply")

    task = ctx.store.load("20260102-030405")
    assert code == 0 and out.strip() == "20260102-030405"
    assert (task.state, task.goal, task.prompt) == (S.PROPOSED, "add multiply", "")


def test_new_in_the_same_second_gets_a_suffix_instead_of_overwriting(ctx):
    run(ctx, "new", "first")
    code, out, _ = run(ctx, "new", "second")

    assert code == 0 and out.strip() == "20260102-030405-2"
    assert ctx.store.load("20260102-030405").goal == "first"


def test_list_shows_id_state_and_failure_reason_and_names_skipped_files(ctx):
    put(ctx, S.FAILED, "a", failure_reason="tests_failed")
    put(ctx, S.REVIEWED, "b")
    (ctx.settings.tasks_dir / "broken.json").write_text("{nope")

    code, out, _ = run(ctx, "list")

    assert code == 0
    assert "a  failed  tests_failed" in out and "b  reviewed" in out and "skipped: broken.json" in out


def test_show_prints_the_task_details(ctx):
    put(ctx, S.AUDITED, goal="g", plan="the plan", plan_risks=("r1",), review_findings=("f1",),
        audit_findings=("a1",), worktree="/w/t1", plan_error="old")

    code, out, _ = run(ctx, "show", "t1")

    assert code == 0
    for text in ("audited", "g", "the plan", "r1", "f1", "a1", "/w/t1", "factory/t1", "old"):
        assert text in out


@pytest.mark.parametrize("command", ["show", "advance", "approve", "reject", "costs"])
@pytest.mark.parametrize("task_id", ["missing", "../evil"])
def test_unknown_or_unsafe_ids_exit_2(ctx, command, task_id):
    code, _, err = run(ctx, command, task_id)

    assert code == 2 and task_id in err


def test_usage_errors_exit_2(ctx):
    assert run(ctx)[0] == 2
    assert run(ctx, "bogus")[0] == 2


def test_control_characters_are_escaped_except_newline_and_tab():
    text = "ok\n\tfine \x1b[2K hidden\rline ‮"

    escaped = escape_control(text)

    assert escaped == "ok\n\tfine \\x1b[2K hidden\\x0dline \\u202e"


def test_show_escapes_control_characters_in_agent_text(ctx):
    put(ctx, S.REVIEWED, goal="g", plan="line\x1b[8mhidden")

    _, out, _ = run(ctx, "show", "t1")

    assert "\x1b" not in out and "\\x1b[8mhidden" in out


# --- advance ------------------------------------------------------------------

def moving(ctx, to, **fields):
    def stage(task_id):
        task = replace(ctx.store.load(task_id), state=to, **fields)
        ctx.store.save(task)
        return task
    return stage


def all_stages(ctx):
    return {"plan": moving(ctx, S.PLANNED), "review": moving(ctx, S.REVIEWED), "implement": moving(ctx, S.AGENT_DONE),
            "verify": moving(ctx, S.VERIFIED), "audit": moving(ctx, S.AUDITED)}


def test_advance_prints_steps_and_exits_0_at_reviewed_pointing_at_approve(ctx):
    put(ctx)
    ctx.stages.update(all_stages(ctx))

    code, out, _ = run(ctx, "advance", "t1")

    assert code == 0
    assert "plan -> planned" in out and "review -> reviewed" in out and "factory approve t1" in out


def test_advance_exits_0_at_audited(ctx):
    put(ctx, S.QUEUED)
    ctx.stages.update(all_stages(ctx))

    assert run(ctx, "advance", "t1")[0] == 0


def test_advance_exits_1_when_the_task_fails_and_when_a_stage_stops(ctx):
    put(ctx, S.QUEUED)
    ctx.stages.update({**all_stages(ctx), "implement": moving(ctx, S.FAILED, failure_reason="timeout")})
    assert run(ctx, "advance", "t1")[0] == 1

    put(ctx)
    ctx.stages.update({**all_stages(ctx), "plan": lambda task_id: ctx.store.load(task_id)})
    code, out, _ = run(ctx, "advance", "t1")
    assert code == 1 and "plan: no progress" in out


def test_advance_refuses_to_run_while_another_driver_holds_the_lock(ctx, monkeypatch):
    put(ctx)
    ctx.stages.update(all_stages(ctx))

    def locked(*a, **k):
        raise cli.AlreadyRunning("state")

    monkeypatch.setattr("factory.cli.state_lock", locked)

    code, _, err = run(ctx, "advance", "t1")

    assert code == 1 and "another factory" in err and ctx.store.load("t1").state is S.PROPOSED


def test_advance_holds_the_lock_while_the_stages_run(ctx, monkeypatch):
    from contextlib import contextmanager
    events = []

    @contextmanager
    def fake_lock(directory):
        events.append("lock")
        yield
        events.append("unlock")

    monkeypatch.setattr("factory.cli.state_lock", fake_lock)
    put(ctx)
    ctx.stages.update({**all_stages(ctx), "plan": lambda tid: (events.append("plan"), moving(ctx, S.PLANNED)(tid))[1]})

    run(ctx, "advance", "t1")

    assert events[:2] == ["lock", "plan"] and events[-1] == "unlock"


# --- reject / costs -----------------------------------------------------------

def test_reject_fails_a_reviewed_task_without_needing_a_terminal(ctx):
    put(ctx, S.REVIEWED)

    code, _, _ = run(ctx, "reject", "t1", tty=False)

    task = ctx.store.load("t1")
    assert code == 0 and (task.state, task.failure_reason) == (S.FAILED, "plan_rejected_by_human")


def test_reject_of_a_task_that_is_not_reviewed_exits_1(ctx):
    put(ctx, S.PLANNED)

    code, _, err = run(ctx, "reject", "t1")

    assert code == 1 and "planned" in err and ctx.store.load("t1").state is S.PLANNED


def test_costs_totals_by_stage_and_task_and_flags_assumed_entries(ctx):
    put(ctx)
    ctx.ledger.append("t1", 0.1, stage="plan")
    ctx.ledger.append("t1", 0.2, stage="plan")
    ctx.ledger.append("t2", 0.6, assumed=True, stage="audit")
    ctx.ledger.append("t2", 0.05)

    code, out, _ = run(ctx, "costs")

    assert code == 0
    assert "plan  $0.3000" in out and "audit  $0.6000" in out and "unknown  $0.0500" in out
    assert "t1  $0.3000" in out and "t2  $0.6500" in out and "assumed: $0.6000" in out and "total: $0.9500" in out


def test_costs_for_one_task_only_counts_that_task(ctx):
    put(ctx)
    ctx.ledger.append("t1", 0.1, stage="plan")
    ctx.ledger.append("t2", 0.9, stage="plan")

    _, out, _ = run(ctx, "costs", "t1")

    assert "total: $0.1000" in out and "t2" not in out


# --- approve ------------------------------------------------------------------

PLAN = "1. add multiply\n2. add tests"


def reviewed(ctx, plan=PLAN):
    put(ctx, S.REVIEWED, goal="add multiply", plan=plan, plan_digest=plan_digest(plan),
        plan_risks=("risky",), review_findings=("looks fine",))


@pytest.fixture
def approve_spy(monkeypatch):
    calls = []
    monkeypatch.setattr("factory.cli.approve_plan", lambda *a, **k: calls.append(a))
    return calls


def test_approve_refuses_without_an_interactive_terminal_and_does_nothing(ctx, approve_spy):
    reviewed(ctx)

    code, _, err = run(ctx, "approve", "t1", stdin="y\n", tty=False)

    assert code == 1 and "interactive" in err and approve_spy == []


def test_approve_displays_goal_plan_risks_and_findings_then_approves_on_y(ctx, approve_spy):
    reviewed(ctx)

    code, out, _ = run(ctx, "approve", "t1", stdin="  y \n")

    assert code == 0
    for text in ("add multiply", PLAN, "risky", "looks fine"):
        assert text in out
    assert approve_spy == [("t1", ctx.store, plan_digest(PLAN))]


@pytest.mark.parametrize("answer", ["n\n", "\n", "yes\n", "", "Y y\n"])
def test_anything_but_y_or_eof_does_not_approve(ctx, approve_spy, answer):
    reviewed(ctx)

    code, _, _ = run(ctx, "approve", "t1", stdin=answer)

    assert code == 1 and approve_spy == []


def test_the_digest_is_of_the_stored_plan_even_when_it_contains_escaped_characters(ctx, approve_spy):
    plan = "step\x1b[8m hidden"
    reviewed(ctx, plan)

    code, out, _ = run(ctx, "approve", "t1", stdin="y\n")

    assert "\x1b" not in out and approve_spy[0][2] == plan_digest(plan)


def test_a_digest_mismatch_exits_1_saying_the_plan_changed(ctx, monkeypatch):
    reviewed(ctx)

    def mismatch(*a):
        raise ValueError("plan digest mismatch")

    monkeypatch.setattr("factory.cli.approve_plan", mismatch)

    code, _, err = run(ctx, "approve", "t1", stdin="y\n")

    assert code == 1 and "plan changed since display" in err


def test_approve_of_a_task_that_is_not_reviewed_exits_1_without_asking(ctx, approve_spy):
    put(ctx, S.PLANNED)

    code, _, err = run(ctx, "approve", "t1", stdin="y\n")

    assert code == 1 and "planned" in err and approve_spy == []


def test_a_real_approval_queues_the_task(ctx):
    reviewed(ctx)

    code, _, _ = run(ctx, "approve", "t1", stdin="y\n")

    assert code == 0 and ctx.store.load("t1").state is S.QUEUED


@pytest.mark.parametrize("command", [["new", "g"], ["list"], ["show", "t1"], ["advance", "t1"], ["reject", "t1"],
                                      ["costs"]])
def test_no_command_except_approve_calls_approve_plan(ctx, approve_spy, command):
    reviewed(ctx)
    ctx.stages.update(all_stages(ctx))

    run(ctx, *command, stdin="y\n")

    assert approve_spy == []


# --- merge --------------------------------------------------------------------

@pytest.fixture
def merge_spy(ctx, monkeypatch):
    from factory.merge_task import Preview
    calls = []
    audited_task = Task(id="t1", prompt="", state=S.AUDITED, audited_sha="abc123", audit_findings=("fine\x1b[31m",))
    preview = Preview(audited_task, "factory/t1", " evil\x1b[2J.py | 1 +\n 1 file changed", audited_task.audit_findings)
    monkeypatch.setattr("factory.cli.merge_preview", lambda *a, **k: preview)
    monkeypatch.setattr("factory.cli.merge_task", lambda *a, **k: calls.append((a, k)) or replace(audited_task, state=S.MERGED))
    put(ctx, S.AUDITED, audited_sha="abc123")
    return calls


def test_merge_refuses_without_an_interactive_terminal_and_does_nothing(ctx, merge_spy):
    code, _, err = run(ctx, "merge", "t1", stdin="y\n", tty=False)

    assert code == 1 and "interactive" in err and merge_spy == []


def test_merge_shows_branch_diffstat_and_findings_escaped_then_merges_the_shown_sha_on_y(ctx, merge_spy):
    code, out, _ = run(ctx, "merge", "t1", stdin="y\n")

    assert code == 0 and "factory/t1" in out and "1 file changed" in out and "\x1b" not in out
    assert "sign" in out
    (args, kwargs), = merge_spy
    assert args[0] == "t1" and kwargs["shown_sha"] == "abc123"


@pytest.mark.parametrize("answer", ["", "n\n", "yes\n"])
def test_anything_but_y_does_not_merge(ctx, merge_spy, answer):
    code, _, _ = run(ctx, "merge", "t1", stdin=answer)

    assert code == 1 and merge_spy == []


def test_a_refused_merge_exits_1_with_the_reason(ctx, merge_spy, monkeypatch):
    def refuse(*a, **k):
        raise cli.MergeRefused("main moved since the plan; re-run the task")
    monkeypatch.setattr("factory.cli.merge_task", refuse)

    code, _, err = run(ctx, "merge", "t1", stdin="y\n")

    assert code == 1 and "main moved" in err


def test_merge_exits_1_when_another_run_holds_the_lock(ctx, merge_spy):
    from factory.lock import state_lock
    with state_lock(ctx.settings.state_dir):
        # flock is per open file description, so a second open in this process is refused
        code, _, err = run(ctx, "merge", "t1", stdin="y\n")

    assert code == 1 and "lock" in err and merge_spy == []


@pytest.mark.parametrize("state", [S.MERGED, S.VERIFIED, S.FAILED])
def test_merge_of_a_task_that_is_not_audited_says_which_state_it_is_in(ctx, merge_spy, state):
    put(ctx, state, "t2")

    code, _, err = run(ctx, "merge", "t2", stdin="y\n")

    assert code == 1 and f"t2 is {state.value}, not audited" in err and merge_spy == []


# --- retry --------------------------------------------------------------------

def test_retry_prints_the_new_task_id(ctx):
    put(ctx, S.FAILED, goal="add multiply")

    code, out, _ = run(ctx, "retry", "t1")

    assert code == 0 and out.strip() == "20260102-030405"
    assert ctx.store.load("20260102-030405").retry_of == "t1"


def test_retry_of_a_task_that_cannot_be_retried_exits_1_with_the_reason(ctx):
    put(ctx, S.QUEUED, goal="g")

    code, _, err = run(ctx, "retry", "t1")

    assert code == 1 and "queued" in err


def test_show_prints_retry_of(ctx):
    put(ctx, S.PROPOSED, goal="g", retry_of="t0")

    assert "retry_of: t0" in run(ctx, "show", "t1")[1]


# --- recover ------------------------------------------------------------------

def test_recover_fails_the_running_task_and_warns_about_the_agent_and_lost_work(ctx):
    put(ctx, S.RUNNING, worktree="/w/t1")
    (ctx.settings.state_dir).mkdir(parents=True, exist_ok=True)
    (ctx.settings.state_dir / "lock").write_text("4242")

    code, out, _ = run(ctx, "recover", "t1")

    assert code == 0 and ctx.store.load("t1").failure_reason == "interrupted"
    assert "4242" in out and "/w/t1" in out and "still be alive" in out and "uncommitted" in out


def test_recover_exits_1_when_another_run_holds_the_lock_and_changes_nothing(ctx):
    from factory.lock import state_lock
    put(ctx, S.RUNNING)

    with state_lock(ctx.settings.state_dir):
        code, _, err = run(ctx, "recover", "t1")

    assert code == 1 and "lock" in err
    assert ctx.store.load("t1").state is S.RUNNING and ctx.ledger.entries() == []


def test_recover_of_a_task_that_is_not_running_exits_1_with_the_reason(ctx):
    put(ctx, S.AUDITED)

    code, _, err = run(ctx, "recover", "t1")

    assert code == 1 and "audited" in err


# --- clean --------------------------------------------------------------------

@pytest.fixture
def clean_spy(ctx, monkeypatch):
    from factory.clean_tasks import Candidate, Outcome
    calls = []
    failed = Task(id="t1", prompt="", state=S.FAILED, worktree="/w/t1")
    candidates = [Candidate(failed, directory_exists=True, dirty=True, commits_ahead=2, branch_exists=True)]
    monkeypatch.setattr("factory.cli.clean_candidates", lambda *a, **k: candidates)
    monkeypatch.setattr("factory.cli.clean_tasks", lambda *a, **k: calls.append(a) or [Outcome("t1", done=["removed worktree"])])
    return calls


def test_clean_refuses_without_an_interactive_terminal_and_does_nothing(ctx, clean_spy):
    code, _, err = run(ctx, "clean", stdin="y\n", tty=False)

    assert code == 1 and "interactive" in err and clean_spy == []


def test_clean_shows_each_candidate_with_its_flags_and_cleans_on_y(ctx, clean_spy):
    code, out, _ = run(ctx, "clean", stdin="y\n")

    assert code == 0 and "t1" in out and "failed" in out and "dirty" in out and "2 commits ahead" in out
    assert "removed worktree" in out and len(clean_spy) == 1


@pytest.mark.parametrize("answer", ["", "n\n", "yes\n"])
def test_anything_but_y_does_not_clean(ctx, clean_spy, answer):
    code, _, _ = run(ctx, "clean", stdin=answer)

    assert code == 1 and clean_spy == []


def test_clean_exits_1_when_another_run_holds_the_lock(ctx, clean_spy):
    from factory.lock import state_lock
    with state_lock(ctx.settings.state_dir):
        code, _, err = run(ctx, "clean", stdin="y\n")

    assert code == 1 and "lock" in err and clean_spy == []


def test_clean_with_nothing_to_clean_says_so_and_exits_0(ctx, monkeypatch):
    monkeypatch.setattr("factory.cli.clean_candidates", lambda *a, **k: [])

    code, out, _ = run(ctx, "clean", stdin="y\n")

    assert code == 0 and "nothing to clean" in out


def test_clean_exits_1_and_prints_the_problems_when_a_task_could_not_be_cleaned(ctx, clean_spy, monkeypatch):
    from factory.clean_tasks import Outcome
    monkeypatch.setattr("factory.cli.clean_tasks", lambda *a, **k: [Outcome("t1", problems=["worktree kept: git failed"])])

    code, out, _ = run(ctx, "clean", stdin="y\n")

    assert code == 1 and "worktree kept" in out


# --- abandon ------------------------------------------------------------------

def test_abandon_fails_the_task_and_says_clean_removes_the_worktree_and_an_agent_may_live(ctx):
    put(ctx, S.AGENT_DONE, worktree="/w/t1")

    code, out, _ = run(ctx, "abandon", "t1")

    task = ctx.store.load("t1")
    assert code == 0 and (task.state, task.failure_reason) == (S.FAILED, "abandoned")
    assert "factory clean" in out and "may have left an agent running" in out


def test_abandon_of_a_refused_task_exits_1_with_the_reason_and_changes_nothing(ctx):
    put(ctx, S.RUNNING)

    code, _, err = run(ctx, "abandon", "t1")

    assert code == 1 and "recover" in err and ctx.store.load("t1").state is S.RUNNING


def test_abandon_exits_1_when_another_run_holds_the_lock_and_changes_nothing(ctx):
    from factory.lock import state_lock
    put(ctx, S.QUEUED)

    with state_lock(ctx.settings.state_dir):
        code, _, err = run(ctx, "abandon", "t1")

    assert code == 1 and "lock" in err and ctx.store.load("t1").state is S.QUEUED


@pytest.mark.parametrize("task_id", ["missing", "../evil"])
def test_abandon_of_an_unknown_or_unsafe_id_exits_2(ctx, task_id):
    code, _, err = run(ctx, "abandon", task_id)

    assert code == 2 and task_id in err
