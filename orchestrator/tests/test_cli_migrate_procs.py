import os

from factory.process_registry import Record, write_record
from test_cli_migrate import ctx, run, seed_old_files  # noqa: F401  (ctx is a fixture)


def old_record(state_dir, pid=7, **overrides):
    fields = dict(pid=pid, pgid=pid, task_id="t1", stage="implement", launched_after=100.0, launched_before=100.5)
    write_record(state_dir, Record(**{**fields, **overrides}))
    return state_dir / "procs" / f"{pid}.json"


def nothing_is_running(ctx):
    ctx.probe = lambda pid: None
    ctx.group_alive = lambda pgid: False


def test_migrate_removes_an_old_file_record_whose_process_is_gone_and_says_so(ctx):
    nothing_is_running(ctx)
    path = old_record(ctx.settings.state_dir)

    code, out, _ = run(ctx, "migrate")

    assert code == 0 and not path.exists()
    assert "removed stale process record 7.json\n" in out


def test_guard_migrate_refuses_and_names_the_record_of_a_live_process_and_changes_nothing(ctx):
    ctx.probe = lambda pid: 100.2  # inside the launch window
    ctx.group_alive = lambda pgid: True
    live, gone = old_record(ctx.settings.state_dir), old_record(ctx.settings.state_dir, pid=8, launched_after=500.0, launched_before=500.5)
    seed_old_files(ctx.settings)

    code, out, err = run(ctx, "migrate")

    assert code == 1 and out == "" and "pid 7 (task t1, implement) may still be running" in err and "nothing changed" in err
    assert live.exists() and gone.exists() and ctx.store.list().tasks == []


def test_guard_migrate_refuses_when_the_leader_is_gone_but_its_group_lives(ctx):
    ctx.probe = lambda pid: None
    ctx.group_alive = lambda pgid: True
    path = old_record(ctx.settings.state_dir)

    code, _, err = run(ctx, "migrate")

    assert code == 1 and "pid 7" in err and path.exists()


def test_guard_migrate_refuses_when_the_process_table_cannot_be_read(ctx):
    from factory.process_registry import ProbeFailed

    def broken(pid):
        raise ProbeFailed("cannot run ps")
    ctx.probe = broken
    path = old_record(ctx.settings.state_dir)

    code, _, err = run(ctx, "migrate")

    assert code == 1 and "cannot tell whether pid 7" in err and "cannot run ps" in err and path.exists()


def test_guard_a_reused_pid_outside_the_launch_window_counts_as_gone(ctx):
    ctx.probe = lambda pid: 9999.0
    ctx.group_alive = lambda pgid: False
    path = old_record(ctx.settings.state_dir)

    code, _, _ = run(ctx, "migrate")

    assert code == 0 and not path.exists()


def test_migrate_deletes_and_reports_an_unreadable_record(ctx):
    nothing_is_running(ctx)
    (ctx.settings.state_dir / "procs").mkdir()
    broken = ctx.settings.state_dir / "procs" / "9.json"
    broken.write_text("{not json")

    code, out, _ = run(ctx, "migrate")

    assert code == 0 and not broken.exists() and "removed invalid process record 9.json" in out


def test_migrate_reports_a_leftover_temp_file_and_leaves_it_as_it_may_describe_a_live_agent(ctx):
    nothing_is_running(ctx)
    (ctx.settings.state_dir / "procs").mkdir()
    temporary = ctx.settings.state_dir / "procs" / ".7.tmp"
    temporary.write_text("{")

    code, _, err = run(ctx, "migrate")

    assert code == 1 and ".7.tmp" in err and temporary.exists()


def test_migrate_is_refused_while_another_run_holds_the_lock(ctx):
    from factory.lock import state_lock
    nothing_is_running(ctx)
    path = old_record(ctx.settings.state_dir)

    with state_lock(ctx.settings.state_dir):
        code, _, err = run(ctx, "migrate")

    assert code == 1 and "holds the lock" in err and path.exists()

