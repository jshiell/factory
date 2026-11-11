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
