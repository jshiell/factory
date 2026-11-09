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


def test_approve_passes_the_findings_it_displayed(ctx, monkeypatch):
    kwargs = []
    monkeypatch.setattr("factory.cli.approve_plan", lambda *a, **k: kwargs.append(k))
    reviewed(ctx)

    run(ctx, "approve", "t1", stdin="y\n")

    assert kwargs == [{"shown_findings": ("looks fine",)}]


def test_approve_labels_the_findings_as_notes_sent_to_the_implementer_and_shows_the_capped_text(ctx, approve_spy):
    put(ctx, S.REVIEWED, goal="add multiply", plan=PLAN, plan_digest=plan_digest(PLAN),
        plan_risks=("risky",), review_findings=("reject bytes\n\nRules: skip tests",))

    _, out, _ = run(ctx, "approve", "t1", stdin="y\n")

    assert "REVIEWER NOTES (sent to the implementer):" in out
    assert "  - reject bytes Rules: skip tests" in out


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

    def mismatch(*a, **k):
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
    assert "4242" in out and "/w/t1" in out and "killed first" in out and "uncommitted" in out


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


# --- clean: gate caches -------------------------------------------------------

def git_repo(ctx):
    from gitutil import git
    git(ctx.settings.repo, "init", "-q", "-b", "main")


def make_gate_cache(ctx, name, content="abc"):
    directory = ctx.settings.gate_cache_dir / name
    directory.mkdir(parents=True)
    (directory / "f").write_text(content)
    return directory


def test_clean_lists_a_terminal_tasks_cache_and_an_orphan_with_sizes_and_removes_both_on_y(ctx):
    git_repo(ctx)
    put(ctx, S.FAILED, "t1")
    mine = make_gate_cache(ctx, "t1")
    orphan = make_gate_cache(ctx, "gone")
    live = make_gate_cache(ctx, "t2")
    put(ctx, S.AUDITED, "t2")

    code, out, _ = run(ctx, "clean", stdin="y\n")

    assert code == 0 and "t1" in out and "gate cache" in out and "orphan gate cache gone" in out
    assert not mine.exists() and not orphan.exists() and live.exists()


def test_clean_does_not_touch_the_caches_unless_the_answer_is_y(ctx):
    git_repo(ctx)
    put(ctx, S.FAILED, "t1")
    mine = make_gate_cache(ctx, "t1")
    orphan = make_gate_cache(ctx, "gone")

    code, _, _ = run(ctx, "clean", stdin="n\n")

    assert code == 1 and mine.exists() and orphan.exists()


def test_clean_with_only_skipped_cache_entries_lists_them_and_exits_1_without_asking(ctx):
    import os
    ctx.settings.gate_cache_dir.mkdir(parents=True)
    os.symlink(ctx.settings.state_dir, ctx.settings.gate_cache_dir / "linked")

    code, out, _ = run(ctx, "clean", stdin="y\n")

    assert code == 1 and "skipped cache entry linked" in out and "Clean these?" not in out


def test_clean_with_no_worktrees_branches_or_caches_says_nothing_to_clean(ctx):
    code, out, _ = run(ctx, "clean", stdin="y\n")

    assert code == 0 and "nothing to clean" in out


def test_clean_holding_the_lock_leaves_the_caches_alone(ctx):
    from factory.lock import state_lock
    orphan = make_gate_cache(ctx, "gone")

    with state_lock(ctx.settings.state_dir):
        code, _, err = run(ctx, "clean", stdin="y\n")

    assert code == 1 and "lock" in err and orphan.exists()


def describe(**fields):
    from factory.cli import _describe_candidate
    from factory.clean_tasks import Candidate
    defaults = dict(directory_exists=True, dirty=False, commits_ahead=0, branch_exists=True)
    task = Task(id="t1", prompt="", state=S.FAILED, worktree="/w/t1")
    return _describe_candidate(Candidate(task, **{**defaults, **fields}))


def test_an_empty_failed_branch_is_previewed_as_will_be_deleted_not_left_behind():
    line = describe(branch_empty=True)

    assert "empty branch will be deleted" in line and "left behind" not in line


def test_a_failed_branch_with_commits_is_still_left_behind():
    line = describe(commits_ahead=2, branch_empty=False)

    assert "branch left behind" in line and "will be deleted" not in line


def test_an_outside_root_task_does_not_promise_a_branch_deletion():
    assert "will be deleted" not in describe(branch_empty=True, outside_root=True)


def test_one_commit_is_singular():
    assert "1 commit ahead of base" in describe(commits_ahead=1) and "1 commits" not in describe(commits_ahead=1)


# --- rebase -------------------------------------------------------------------

def audited_with_main_moved(ctx):
    from gitutil import git
    from factory.worktrees import WorktreeManager
    repo = ctx.settings.repo
    git_repo(ctx)
    (repo / "a.txt").write_text("a")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", "init")
    worktree, base = WorktreeManager(repo, ctx.settings.worktree_root).create("t1")
    (worktree / "b.txt").write_text("b")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "Task t1")
    tip = git(worktree, "rev-parse", "HEAD")
    put(ctx, S.AUDITED, worktree=str(worktree), base_sha=base, audited_sha=tip, goal="g")
    (repo / "m.txt").write_text("m")
    git(repo, "add", "m.txt")
    git(repo, "commit", "-q", "-m", "main moved")
    return tip


def test_rebase_sends_the_task_back_to_agent_done_names_the_old_commit_and_needs_no_tty(ctx):
    old = audited_with_main_moved(ctx)

    code, out, _ = run(ctx, "rebase", "t1", tty=False)

    task = ctx.store.load("t1")
    assert code == 0 and task.state is S.AGENT_DONE and task.audited_sha is None
    assert old in out and "factory advance" in out


def test_rebase_of_a_refused_task_exits_1_with_the_reason_and_changes_nothing(ctx):
    put(ctx, S.AUDITED, worktree=None)

    code, _, err = run(ctx, "rebase", "t1")

    assert code == 1 and "no worktree" in err and ctx.store.load("t1").state is S.AUDITED


def test_rebase_exits_1_when_another_run_holds_the_lock_and_changes_nothing(ctx):
    from factory.lock import state_lock
    audited_with_main_moved(ctx)

    with state_lock(ctx.settings.state_dir):
        code, _, err = run(ctx, "rebase", "t1")

    assert code == 1 and "lock" in err and ctx.store.load("t1").state is S.AUDITED


@pytest.mark.parametrize("task_id", ["missing", "../evil"])
def test_rebase_of_an_unknown_or_unsafe_id_exits_2(ctx, task_id):
    code, _, err = run(ctx, "rebase", task_id)

    assert code == 2 and task_id in err


def test_rebase_refusal_escapes_control_characters_from_the_quoted_git_line(ctx, monkeypatch):
    from factory import cli
    from factory.rebase_task import RebaseRefused
    put(ctx, S.AUDITED)

    def refuse(*args, **kwargs):
        raise RebaseRefused("git rebase failed (CONFLICT (content): Merge conflict in \x1b[31mevil\x1b[0m.txt)")

    monkeypatch.setattr(cli, "rebase_task", refuse)

    code, _, err = run(ctx, "rebase", "t1")

    assert code == 1 and "\x1b" not in err and "evil" in err


def failed_with_empty_branch_after_main_was_rewritten(ctx):
    from gitutil import git
    from factory.worktrees import WorktreeManager
    repo = ctx.settings.repo
    git_repo(ctx)
    (repo / "a.txt").write_text("a")
    git(repo, "add", "a.txt")
    git(repo, "commit", "-q", "-m", "init")
    worktree, base = WorktreeManager(repo, ctx.settings.worktree_root).create("t1")
    put(ctx, S.FAILED, worktree=str(worktree), base_sha=base)
    rewritten = git(repo, "commit-tree", "-m", "rewritten main", git(repo, "hash-object", "-t", "tree", "/dev/null"))
    git(repo, "reset", "-q", "--hard", rewritten)  # the base is no longer an ancestor of main: `branch -d` would refuse


def test_clean_exits_0_after_deleting_an_empty_failed_branch_that_git_branch_d_would_refuse(ctx):
    """Real git through the CLI (the spy tests above cannot show this). Starts green: R2 already fixed the behaviour."""
    from gitutil import git
    failed_with_empty_branch_after_main_was_rewritten(ctx)

    code, out, _ = run(ctx, "clean", stdin="y\n")

    assert code == 0 and "deleted branch" in out
    assert git(ctx.settings.repo, "branch", "--list", "factory/t1") == ""


def test_a_merged_task_with_an_empty_branch_is_not_previewed_as_will_be_deleted(ctx):
    from factory.clean_tasks import Candidate
    from factory.cli import _describe_candidate
    merged = Task(id="t1", prompt="", state=S.MERGED, base_sha="b")

    line = _describe_candidate(Candidate(merged, True, False, 0, True, branch_empty=True))

    assert "empty branch will be deleted" not in line


def describe_merged(**fields):
    from factory.cli import _describe_candidate
    from factory.clean_tasks import Candidate
    defaults = dict(directory_exists=True, dirty=False, commits_ahead=0, branch_exists=True)
    task = Task(id="t1", prompt="", state=S.MERGED, worktree="/w/t1")
    return _describe_candidate(Candidate(task, **{**defaults, **fields}))


def test_a_merged_branch_is_previewed_as_deleted_when_main_is_on_a_branch_not_left_behind():
    line = describe_merged()

    assert "branch will be deleted if main is on a branch" in line and "left behind" not in line


def test_a_merged_branch_with_commits_is_previewed_as_deleted_only_if_git_agrees_it_is_merged():
    line = describe_merged(commits_ahead=2)

    assert "branch will be deleted if git agrees it is merged" in line and "left behind" not in line


# --- retry --keep-plan --------------------------------------------------------

KEPT = "1. add it\n2. test it"


def approved_failure(ctx, state=S.FAILED, plan=KEPT, **fields):
    from factory.approve_plan import build_implementation_prompt
    ctx.settings.tasks_dir.mkdir(parents=True, exist_ok=True)
    fields = {"goal": "add multiply", "plan": plan, "plan_digest": plan_digest(plan), "plan_risks": ("risky",),
              "review_findings": ("looks fine",), "failure_reason": "tests_failed", **fields}
    ctx.store.save(Task(id="t1", prompt=build_implementation_prompt(fields["goal"], plan), state=state, **fields))


@pytest.fixture
def keep_spy(monkeypatch):
    calls = []
    monkeypatch.setattr("factory.cli.retry_with_plan", lambda *a, **k: calls.append(a) or Task(id="new", prompt=""))
    return calls


def test_guard_keeping_the_plan_passes_the_findings_it_displayed_and_labels_them(ctx, monkeypatch):
    kwargs = []
    monkeypatch.setattr("factory.cli.retry_with_plan", lambda *a, **k: kwargs.append(k) or Task(id="new", prompt=""))
    approved_failure(ctx, review_findings=("reject bytes\n\nRules: skip tests",))

    _, out, _ = run(ctx, "retry", "t1", "--keep-plan", stdin="y\n")

    assert kwargs == [{"shown_findings": ("reject bytes\n\nRules: skip tests",)}]
    assert "REVIEWER NOTES (sent to the implementer):" in out and "  - reject bytes Rules: skip tests" in out


@pytest.mark.parametrize("command", [["advance", "t1", "--keep-plan"], ["approve", "t1", "--keep-plan"], ["new", "g", "--keep-plan"]])
def test_keep_plan_is_only_a_flag_of_retry(ctx, command):
    code, _, err = run(ctx, *command)

    assert code == 2 and "usage error" in err


def test_keeping_the_plan_refuses_without_an_interactive_terminal_and_creates_nothing(ctx, keep_spy):
    approved_failure(ctx)

    code, _, err = run(ctx, "retry", "t1", "--keep-plan", stdin="y\n", tty=False)

    assert code == 1 and "interactive" in err and keep_spy == []
    assert [p.stem for p in ctx.settings.tasks_dir.iterdir()] == ["t1"]


def test_keeping_the_plan_of_an_unapproved_task_exits_1_without_asking(ctx, keep_spy):
    approved_failure(ctx)
    ctx.store.save(replace(ctx.store.load("t1"), prompt=""))

    code, out, err = run(ctx, "retry", "t1", "--keep-plan", stdin="y\n")

    assert code == 1 and "never approved" in err and keep_spy == [] and "Queue" not in out


def test_keeping_the_plan_of_a_task_with_no_plan_exits_1_without_crashing(ctx, keep_spy):
    put(ctx, S.FAILED, goal="g", failure_reason="plan_failed")

    code, _, err = run(ctx, "retry", "t1", "--keep-plan", stdin="y\n")

    assert code == 1 and "plan" in err and keep_spy == []


def test_keeping_the_plan_shows_the_plan_and_context_then_creates_a_queued_task_on_y(ctx):
    approved_failure(ctx, failure_reason="tests_failed")

    code, out, _ = run(ctx, "retry", "t1", "--keep-plan", stdin=" y \n")

    assert code == 0
    for text in ("add multiply", KEPT, "risky", "looks fine", "retry of t1", "tests_failed"):
        assert text in out
    new = ctx.store.load("20260102-030405")
    assert new.state is S.QUEUED and new.retry_of == "t1" and new.plan == KEPT
    assert "20260102-030405" in out and "factory advance 20260102-030405" in out


@pytest.mark.parametrize("answer", ["n\n", "\n", "yes\n", "", "Y y\n"])
def test_anything_but_y_does_not_keep_the_plan(ctx, keep_spy, answer):
    approved_failure(ctx)

    code, _, _ = run(ctx, "retry", "t1", "--keep-plan", stdin=answer)

    assert code == 1 and keep_spy == []


def test_the_confirmation_binds_to_the_digest_of_the_plan_shown(ctx, keep_spy):
    approved_failure(ctx, plan="step\x1b[8m hidden")

    code, out, _ = run(ctx, "retry", "t1", "--keep-plan", stdin="y\n")

    assert "\x1b" not in out and keep_spy[0][3] == plan_digest("step\x1b[8m hidden")


def test_a_plan_that_changed_after_it_was_shown_creates_nothing(ctx, monkeypatch):
    from factory.retry_task import RetryRefused
    approved_failure(ctx)

    def mismatch(*a, **k):
        raise RetryRefused("plan digest mismatch for t1")

    monkeypatch.setattr("factory.cli.retry_with_plan", mismatch)

    code, _, err = run(ctx, "retry", "t1", "--keep-plan", stdin="y\n")

    assert code == 1 and "plan changed since display" in err


def test_an_audited_source_is_shown_with_its_findings_and_a_mergeable_warning(ctx):
    approved_failure(ctx, S.AUDITED, failure_reason=None, audit_findings=("naming is odd",), audited_sha="abc")

    _, out, _ = run(ctx, "retry", "t1", "--keep-plan", stdin="n\n")

    assert "naming is odd" in out and "still mergeable" in out


@pytest.mark.parametrize("command", [["new", "g"], ["list"], ["show", "t1"], ["advance", "t1"], ["reject", "t1"],
                                      ["approve", "t1"], ["retry", "t1"], ["costs"]])
def test_guard_no_command_except_retry_keep_plan_calls_retry_with_plan(ctx, keep_spy, command):
    approved_failure(ctx)
    ctx.stages.update(all_stages(ctx))

    run(ctx, *command, stdin="y\n")

    assert keep_spy == []


# --- reaping orphans under the lock -------------------------------------------

def reaper(killed=(), discarded=(), uncertain=None):
    from factory.process_registry import ReapResult, ReapUncertain
    calls = []

    def reap(state_dir):
        calls.append(state_dir)
        if uncertain:
            raise ReapUncertain(list(killed), [(r, "ps: operation not permitted") for r in uncertain])
        return ReapResult(list(killed), list(discarded))
    reap.calls = calls
    return reap


def orphan(pid=4242, task_id="t1", stage="implement"):
    from factory.process_registry import Record
    return Record(pid, pid, task_id, stage, 100.0, 100.5)


REAPING_COMMANDS = [
    (("advance", "t1"), S.PROPOSED), (("recover", "t1"), S.RUNNING), (("abandon", "t1"), S.PROPOSED),
    (("rebase", "t1"), S.AUDITED), (("clean",), S.RUNNING), (("merge", "t1"), S.AUDITED),
]


@pytest.mark.parametrize("argv, state", REAPING_COMMANDS, ids=[c[0][0] for c in REAPING_COMMANDS])
def test_every_command_that_takes_the_lock_reaps_orphans_under_it_once(ctx, argv, state):
    put(ctx, state)
    ctx.stages.update(all_stages(ctx))
    ctx.reap = reaper()

    run(ctx, *argv)

    assert ctx.reap.calls == [ctx.settings.state_dir]


def test_what_was_reaped_is_printed_with_its_task_and_stage(ctx):
    put(ctx, S.PROPOSED)
    ctx.stages.update(all_stages(ctx))
    ctx.reap = reaper(killed=[orphan(4242, "t7", "implement")])

    code, out, _ = run(ctx, "advance", "t1")

    assert "killed orphan of t7 (implement), pid 4242" in out


def test_a_probe_that_cannot_tell_refuses_the_command_and_changes_nothing(ctx):
    put(ctx, S.PROPOSED)
    ran = []
    ctx.stages.update(all_stages(ctx))
    ctx.stages["plan"] = lambda task_id: (ran.append(task_id), ctx.store.load(task_id))[1]
    ctx.reap = reaper(uncertain=[orphan(4242, "t7", "gate")])

    code, out, err = run(ctx, "advance", "t1")

    assert code == 1 and ran == []
    assert "cannot prove these processes dead" in err and "t7" in err and "nothing changed" in err


def test_a_held_lock_reaps_nothing(ctx, monkeypatch):
    put(ctx, S.PROPOSED)
    ctx.reap = reaper()

    def held(state_dir):
        raise cli.AlreadyRunning(str(state_dir))
    monkeypatch.setattr(cli, "state_lock", held)

    code, _, err = run(ctx, "advance", "t1")

    assert code == 1 and "holds the lock" in err and ctx.reap.calls == []


# --- plan 23: locking and reaping a directory other than the state dir ----------

def test_locked_with_an_explicit_directory_locks_and_reaps_that_directory_only(ctx, tmp_path):
    from factory.lock import AlreadyRunning, state_lock
    elsewhere = tmp_path / "evals" / "one"
    ctx.reap = reaper()
    out, err = io.StringIO(), io.StringIO()

    with cli._locked(ctx, IO(FakeStdin(), out, err), state_dir=elsewhere):
        with state_lock(ctx.settings.state_dir):  # the real state dir stays free
            pass
        with pytest.raises(AlreadyRunning):
            with state_lock(elsewhere):
                pass

    assert ctx.reap.calls == [elsewhere]



def test_a_context_has_a_defaulted_eval_runner_factory(ctx):
    assert callable(ctx.eval_runner)


# --- plan 23: factory eval-audit ----------------------------------------------------

import json as _json

from factory.audit_corpus import CASES as EVAL_CASES


def _verdict_json(verdict, cost=0.03):
    return _json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": cost,
                        "structured_output": {"verdict": verdict, "findings": ["because"]}})


class LabelRunner:
    """Judges each case as its label says, except the ids in `wrong`; remembers what it was built with."""

    def __init__(self, wrong=(), cost=0.03, spy=None):
        self.wrong, self.cost, self.spy, self.seen = set(wrong), cost, spy, []

    def run(self, task, config):
        from factory.step import RunResult
        case_id = task.id.split("-", 1)[1]
        label = next(c.label for c in EVAL_CASES if c.id == case_id)
        self.seen.append(case_id)
        if self.spy:
            self.spy()
        verdict = label if case_id not in self.wrong else ("fail" if label == "pass" else "pass")
        return RunResult(_verdict_json(verdict, self.cost), 0)


@pytest.fixture
def evals(ctx):
    ctx.reap = reaper()
    built = []

    def factory(runner):
        def build(settings, eval_dir):
            built.append((settings, eval_dir))
            return runner
        ctx.eval_runner = build
    factory.built = built
    return factory


def eval_dirs(ctx):
    root = ctx.settings.state_dir / "evals"
    return sorted(root.iterdir()) if root.exists() else []


def test_a_clean_eval_prints_its_directory_then_the_report_and_exits_zero(ctx, evals):
    evals(LabelRunner())

    code, out, err = run(ctx, "eval-audit", "--runs", "1")

    (eval_dir,) = eval_dirs(ctx)
    assert code == 0 and eval_dir.name == "20260102T030405"
    assert out.splitlines()[0] == f"eval directory: {eval_dir}"
    assert "false-pass" in out and "false-fail" in out and "no earlier complete run" in out
    assert len(list(eval_dir.glob("case-*-r1.json"))) == 25


def test_the_eval_is_built_with_the_settings_and_its_own_directory_and_reaps_only_that_directory(ctx, evals):
    evals(LabelRunner())

    run(ctx, "eval-audit", "--runs", "1", "--only", "case-01")

    (settings, eval_dir), = evals.built
    assert settings is ctx.settings and eval_dir.parent == ctx.settings.state_dir / "evals"
    assert ctx.reap.calls == [eval_dir]


def test_the_real_state_dir_stays_free_and_untouched_while_an_eval_runs(ctx, evals):
    from factory.lock import state_lock
    free = []

    def spy():
        with state_lock(ctx.settings.state_dir):
            free.append(True)
    evals(LabelRunner(spy=spy))

    run(ctx, "eval-audit", "--runs", "1", "--only", "case-01")

    assert free == [True]
    assert not ctx.settings.tasks_dir.exists() or list(ctx.settings.tasks_dir.iterdir()) == []
    assert not ctx.settings.ledger_path.exists()


def test_a_second_eval_on_the_same_directory_is_refused(ctx, evals):
    from factory.lock import state_lock
    evals(LabelRunner())
    run(ctx, "eval-audit", "--runs", "1", "--only", "case-01")
    (eval_dir,) = eval_dirs(ctx)

    with state_lock(eval_dir):
        code, _, err = run(ctx, "eval-audit", "--runs", "1", "--only", "case-01", "--resume", str(eval_dir))

    assert code == 1 and "holds the lock" in err


@pytest.mark.parametrize("args, message", [
    (("--only", "case-99"), "unknown case"),
    (("--runs", "0"), "--runs"),
    (("--max-usd", "0"), "--max-usd"),
    (("--resume", "/nowhere/at/all"), "--resume"),
    (("--compare", "/nowhere/at/all"), "--compare"),
])
def test_bad_options_are_usage_errors_and_nothing_runs(ctx, evals, args, message):
    runner = LabelRunner()
    evals(runner)

    code, out, err = run(ctx, "eval-audit", *args)

    assert code == 2 and message in err and runner.seen == [] and eval_dirs(ctx) == []


def test_a_bad_case_judged_pass_exits_one(ctx, evals):
    evals(LabelRunner(wrong={"case-09"}))

    code, out, _ = run(ctx, "eval-audit", "--runs", "1")

    assert code == 1 and "case-09" in out


def test_a_run_stopped_by_the_budget_cap_exits_three(ctx, evals):
    evals(LabelRunner(cost=0.5))

    code, out, _ = run(ctx, "eval-audit", "--runs", "1", "--max-usd", "0.7")

    assert code == 3


def test_the_next_eval_is_compared_with_the_last_complete_full_one(ctx, evals):
    evals(LabelRunner())
    run(ctx, "eval-audit", "--runs", "1")
    ctx.clock = lambda: datetime(2026, 1, 3, 3, 4, 5)
    evals(LabelRunner(wrong={"case-01"}))

    code, out, _ = run(ctx, "eval-audit", "--runs", "1")

    assert "compared with 20260102T030405" in out and "case-01: pass -> fail" in out and code == 1


def test_resuming_with_a_changed_corpus_is_refused(ctx, evals, monkeypatch):
    evals(LabelRunner())
    run(ctx, "eval-audit", "--runs", "1", "--only", "case-01")
    (eval_dir,) = eval_dirs(ctx)
    from dataclasses import replace as _replace
    monkeypatch.setattr(cli, "EVAL_CASES", [_replace(EVAL_CASES[0], plan="a different plan"), *EVAL_CASES[1:]])
    runner = LabelRunner()
    evals(runner)

    code, _, err = run(ctx, "eval-audit", "--runs", "1", "--only", "case-01", "--resume", str(eval_dir))

    assert code == 2 and "corpus changed" in err and runner.seen == []



def test_check_corpus_runs_the_gate_on_every_case_runs_no_agents_and_exits_zero_when_all_pass(ctx, evals, monkeypatch):
    from factory.gate import GateResult
    runner = LabelRunner()
    evals(runner)
    calls = []
    monkeypatch.setattr(cli, "check_gate", lambda worktree, base_sha, subdir, argv, timeout, **kw: (calls.append((subdir, argv, timeout)), GateResult(True))[1])

    code, out, _ = run(ctx, "eval-audit", "--check-corpus")

    assert code == 0 and len(calls) == 25 and runner.seen == [] and eval_dirs(ctx) == []
    assert calls[0] == ("target", list(ctx.settings.test_argv), ctx.settings.gate_timeout_s)
    assert "case-01 pass minimal" in out and "25 cases, none blocked" in out


def test_check_corpus_names_a_blocked_case_with_its_reason_and_escaped_output_and_exits_one(ctx, evals, monkeypatch):
    from factory.gate import GateResult
    evals(LabelRunner())

    def gate(worktree, base_sha, subdir, argv, timeout, **kw):
        if worktree.name.endswith("case-02"):
            return GateResult(False, "tests_failed", 1, "\x1b[31m1 failed\x1b[0m")
        return GateResult(True)
    monkeypatch.setattr(cli, "check_gate", gate)

    code, out, _ = run(ctx, "eval-audit", "--check-corpus")

    assert code == 1 and "case-02" in out and "BLOCKED tests_failed" in out and "1 failed" in out and "\x1b" not in out
    assert "blocked: case-02" in out


# --- waiving required notes at approve -----------------------------------------

REQ1 = "Recommend (required): reject bytes with TypeError"
REQ2 = "Recommend (required): use math.fsum so the sum cannot overflow"
OPT = "Recommend (optional): add a docstring"


def reviewed_with_notes(ctx, *notes):
    put(ctx, S.REVIEWED, goal="add multiply", plan=PLAN, plan_digest=plan_digest(PLAN),
        plan_risks=("risky",), review_findings=notes)


def test_approve_numbers_the_required_notes_and_offers_waiving_them(ctx, approve_spy):
    reviewed_with_notes(ctx, REQ1, OPT, REQ2, "plain note")

    _, out, _ = run(ctx, "approve", "t1", stdin="n\n")

    assert f"  - [1] {REQ1}\n" in out and f"  - [2] {REQ2}\n" in out
    assert f"  - {OPT}\n" in out and "  - plain note\n" in out
    assert "'w 1 3'" in out


def test_approve_without_a_required_note_shows_no_numbers_and_no_waive_offer(ctx, approve_spy):
    # guard (green on arrival; mutation-checked)
    reviewed_with_notes(ctx, OPT, "plain note")

    _, out, _ = run(ctx, "approve", "t1", stdin="n\n")

    assert "[1]" not in out and "waive" not in out
    assert out.endswith("Approve this plan? [y/N] not approved\n")


@pytest.fixture
def approve_kwargs(monkeypatch):
    calls = []
    monkeypatch.setattr("factory.cli.approve_plan", lambda *a, **k: calls.append(k))
    return calls


def test_w_shows_what_it_waives_asks_again_and_on_y_approves_with_that_note_waived(ctx, approve_kwargs):
    reviewed_with_notes(ctx, REQ1, OPT, REQ2)

    code, out, _ = run(ctx, "approve", "t1", stdin="w 2\ny\n")

    assert code == 0
    assert approve_kwargs == [{"shown_findings": (REQ1, OPT, REQ2), "waived": (REQ2,)}]
    assert "Waiving" in out and f"  - {REQ2}\n" in out.split("Waiving", 1)[1]
    assert "Waive these and approve? [y/N] " in out


@pytest.mark.parametrize("text", ["w", "w 0", "w 3", "w 1 1", "w x", "w ²", "w -1"])
def test_a_bad_waive_answer_approves_nothing_exits_1_and_says_why(ctx, approve_kwargs, text):
    from factory.waiver import parse_answer

    reviewed_with_notes(ctx, REQ1, OPT, REQ2)

    code, out, err = run(ctx, "approve", "t1", stdin=text + "\ny\n")

    assert code == 1 and approve_kwargs == []
    assert err.strip() == parse_answer(text, 2).reason and "Waiving" not in out
    assert ctx.store.load("t1").state is S.REVIEWED


def test_an_unknown_answer_just_says_not_approved(ctx, approve_kwargs):
    # guard (green on arrival): W is not w, and n is n
    reviewed_with_notes(ctx, REQ1)

    for text in ("W 1", "n", "yes"):
        code, out, err = run(ctx, "approve", "t1", stdin=text + "\n")
        assert code == 1 and out.endswith("not approved\n") and err == ""

    assert approve_kwargs == []


def test_y_with_required_notes_shown_approves_without_a_waiver(ctx, approve_kwargs):
    # guard (green on arrival; mutation-checked)
    reviewed_with_notes(ctx, REQ1, REQ2)

    code, _, _ = run(ctx, "approve", "t1", stdin="y\n")

    assert code == 0 and approve_kwargs == [{"shown_findings": (REQ1, REQ2)}]


@pytest.mark.parametrize("second", ["n\n", "\n", "yes\n", ""])
def test_declining_the_waiver_confirmation_approves_nothing(ctx, approve_kwargs, second):
    # guard (green on arrival; mutation-checked)
    reviewed_with_notes(ctx, REQ1, REQ2)

    code, out, _ = run(ctx, "approve", "t1", stdin="w 1\n" + second)

    assert code == 1 and approve_kwargs == [] and out.endswith("not approved\n")


def test_w_when_no_note_is_required_approves_nothing(ctx, approve_kwargs):
    # guard (green on arrival; mutation-checked)
    reviewed_with_notes(ctx, OPT, "plain note")

    code, _, err = run(ctx, "approve", "t1", stdin="w 1\ny\n")

    assert code == 1 and approve_kwargs == [] and err.strip()


def test_an_invalid_waiver_from_approve_is_reported_as_such_not_as_a_changed_plan(ctx, monkeypatch):
    from factory.waiver import InvalidWaiver

    def refuse(*args, **kwargs):
        raise InvalidWaiver("only a required note can be waived: x")

    monkeypatch.setattr("factory.cli.approve_plan", refuse)
    reviewed_with_notes(ctx, REQ1)

    code, _, err = run(ctx, "approve", "t1", stdin="w 1\ny\n")

    assert code == 1 and "cannot waive" in err and "plan changed" not in err


def test_the_approve_line_names_how_many_required_notes_were_waived(ctx, approve_kwargs):
    reviewed_with_notes(ctx, REQ1, REQ2)

    _, out, _ = run(ctx, "approve", "t1", stdin="w 1 2\ny\n")

    assert "approved and queued (2 required notes waived); run: factory advance t1" in out


# --- waiving required notes at retry --keep-plan ---------------------------------

@pytest.fixture
def keep_kwargs(monkeypatch):
    calls = []
    monkeypatch.setattr("factory.cli.retry_with_plan", lambda *a, **k: calls.append(k) or Task(id="new", prompt=""))
    return calls


def test_keeping_the_plan_numbers_required_notes_and_waives_on_w_then_y(ctx, keep_kwargs):
    approved_failure(ctx, review_findings=(REQ1, OPT, REQ2))

    code, out, _ = run(ctx, "retry", "t1", "--keep-plan", stdin="w 2\ny\n")

    assert code == 0
    assert f"  - [1] {REQ1}\n" in out and f"  - [2] {REQ2}\n" in out and f"  - {OPT}\n" in out
    assert "Waive these and queue? [y/N] " in out
    assert keep_kwargs == [{"shown_findings": (REQ1, OPT, REQ2), "waived": (REQ2,)}]


def test_keeping_the_plan_shows_the_notes_waived_earlier(ctx, keep_kwargs):
    approved_failure(ctx, review_findings=(OPT,), waived_findings=(REQ1,))

    _, out, _ = run(ctx, "retry", "t1", "--keep-plan", stdin="n\n")

    assert f"WAIVED EARLIER (not sent):\n  - {REQ1}\n" in out


def test_y_when_keeping_a_plan_with_required_notes_passes_no_waiver(ctx, keep_kwargs):
    # guard (green on arrival; mutation-checked)
    approved_failure(ctx, review_findings=(REQ1, REQ2))

    code, _, _ = run(ctx, "retry", "t1", "--keep-plan", stdin="y\n")

    assert code == 0 and keep_kwargs == [{"shown_findings": (REQ1, REQ2)}]


@pytest.mark.parametrize("stdin", ["w 1\nn\n", "w 1\n", "w 9\ny\n", "w\ny\n", "w 1 1\ny\n"])
def test_a_declined_or_bad_waive_answer_queues_nothing_when_keeping_a_plan(ctx, keep_kwargs, stdin):
    # guard (green on arrival; mutation-checked)
    approved_failure(ctx, review_findings=(REQ1, REQ2))

    code, out, _ = run(ctx, "retry", "t1", "--keep-plan", stdin=stdin)

    assert code == 1 and keep_kwargs == [] and out.endswith("not queued\n")


def test_an_invalid_waiver_from_retry_is_reported_as_such_not_as_a_changed_plan(ctx, monkeypatch):
    # guard (green on arrival; mutation-checked)
    from factory.waiver import InvalidWaiver

    def refuse(*args, **kwargs):
        raise InvalidWaiver("only a required note can be waived: x")

    monkeypatch.setattr("factory.cli.retry_with_plan", refuse)
    approved_failure(ctx, review_findings=(REQ1,))

    code, _, err = run(ctx, "retry", "t1", "--keep-plan", stdin="w 1\ny\n")

    assert code == 1 and "cannot waive" in err and "plan changed" not in err


# --- audit gaps carried by retry --keep-plan --------------------------------------

GAP = "the empty list was not handled"


def test_keeping_the_plan_after_a_rejected_audit_shows_and_passes_the_gaps_it_will_send(ctx, keep_kwargs):
    approved_failure(ctx, failure_reason="audit_rejected", audit_findings=("empty list\x1b[8m not handled",))

    code, out, _ = run(ctx, "retry", "t1", "--keep-plan", stdin="y\n")

    assert code == 0 and "AUDIT FINDINGS (sent to the next attempt):" in out
    assert "  - empty list\\x1b[8m not handled" in out and "\x1b" not in out
    assert keep_kwargs == [{"shown_findings": ("looks fine",), "shown_audit_gaps": ("empty list\x1b[8m not handled",)}]


def test_guard_other_failures_show_no_gap_block_and_pass_no_gaps(ctx, keep_kwargs):
    # guard (green on arrival; mutation-checked)
    approved_failure(ctx, failure_reason="tests_failed", audit_findings=(GAP,))

    _, out, _ = run(ctx, "retry", "t1", "--keep-plan", stdin="y\n")

    assert "sent to the next attempt" not in out and keep_kwargs == [{"shown_findings": ("looks fine",)}]


def test_a_gap_quoting_a_waived_note_is_reported_as_dropped(ctx, keep_kwargs):
    quoting = f"not handled: {REQ2}"
    approved_failure(ctx, review_findings=(REQ2,), failure_reason="audit_rejected", audit_findings=(quoting, GAP))

    code, out, _ = run(ctx, "retry", "t1", "--keep-plan", stdin="w 1\ny\n")

    assert code == 0 and f"dropped (quotes a waived note): {quoting}\n" in out
    assert keep_kwargs == [{"shown_findings": (REQ2,), "waived": (REQ2,), "shown_audit_gaps": (quoting, GAP)}]


def _required_notes(count):
    return tuple(f"gap {i}. Recommend (required): handle it" for i in range(count))


def test_approve_warns_how_many_required_notes_the_cap_dropped_before_asking(ctx, approve_spy):
    from factory.review_notes import MAX_ITEMS

    put(ctx, S.REVIEWED, goal="add multiply", plan=PLAN, plan_digest=plan_digest(PLAN),
        plan_risks=("risky",), review_findings=_required_notes(MAX_ITEMS + 3))

    _, out, _ = run(ctx, "approve", "t1", stdin="y\n")

    warning = f"WARNING: 3 required note(s) beyond the first {MAX_ITEMS} were dropped"
    assert warning in out and out.index("REVIEWER NOTES") < out.index(warning) < out.index("Approve this plan?")


def test_guard_approve_prints_no_warning_at_the_item_limit(ctx, approve_spy):
    from factory.review_notes import MAX_ITEMS

    put(ctx, S.REVIEWED, goal="add multiply", plan=PLAN, plan_digest=plan_digest(PLAN),
        plan_risks=("risky",), review_findings=_required_notes(MAX_ITEMS))

    _, out, _ = run(ctx, "approve", "t1", stdin="y\n")

    assert "WARNING" not in out


def test_guard_waiving_a_shown_note_still_prints_the_warning_once(ctx, approve_spy):
    from factory.review_notes import MAX_ITEMS

    put(ctx, S.REVIEWED, goal="add multiply", plan=PLAN, plan_digest=plan_digest(PLAN),
        plan_risks=("risky",), review_findings=_required_notes(MAX_ITEMS + 2))

    _, out, _ = run(ctx, "approve", "t1", stdin="w 1\ny\n")

    assert out.count("WARNING: 2 required note(s)") == 1


def test_retry_keeping_the_plan_warns_how_many_stored_required_notes_the_cap_dropped(ctx, monkeypatch):
    from factory.review_notes import MAX_ITEMS

    monkeypatch.setattr("factory.cli.retry_with_plan", lambda *a, **k: Task(id="new", prompt=""))
    approved_failure(ctx, review_findings=_required_notes(MAX_ITEMS + 2))

    _, out, _ = run(ctx, "retry", "t1", "--keep-plan", stdin="y\n")

    warning = f"WARNING: 2 required note(s) beyond the first {MAX_ITEMS} were dropped"
    assert warning in out and out.index("REVIEWER NOTES") < out.index(warning) < out.index("Queue this plan again")


def test_guard_retry_prints_no_warning_for_an_already_capped_stored_list(ctx, monkeypatch):
    from factory.review_notes import MAX_ITEMS, TRUNCATED

    monkeypatch.setattr("factory.cli.retry_with_plan", lambda *a, **k: Task(id="new", prompt=""))
    approved_failure(ctx, review_findings=(*_required_notes(MAX_ITEMS), TRUNCATED))

    _, out, _ = run(ctx, "retry", "t1", "--keep-plan", stdin="y\n")

    assert "WARNING" not in out
