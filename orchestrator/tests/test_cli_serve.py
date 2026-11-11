import io

import pytest

from factory.cli import IO, Context, main
from factory.lock import AlreadyRunning, state_lock
from factory.process_registry import ReapResult, ReapUncertain
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore


@pytest.fixture
def ctx(tmp_path):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt")
    settings.state_dir.mkdir()
    context = Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={})
    context.reap = lambda directory: ReapResult([], [])
    context.served = []
    context.serve = lambda app, host, port: context.served.append((app, host, port))
    return context


def serve(ctx, *argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(["serve", *argv], IO(io.StringIO(), out, err), ctx)
    return code, out.getvalue(), err.getvalue()


def test_serve_listens_on_loopback_port_8765_by_default_and_says_where(ctx):
    code, out, err = serve(ctx)

    assert code == 0
    (_, host, port), = ctx.served
    assert (host, port) == ("127.0.0.1", 8765)
    token_file = ctx.settings.state_dir / "token"
    assert out.strip() == f"serving on http://127.0.0.1:8765; token in {token_file}"
    assert token_file.read_text().strip() not in out


def test_serve_takes_a_port(ctx):
    serve(ctx, "--port", "0")

    assert ctx.served[0][2] == 0


def test_the_app_it_serves_requires_the_token(ctx):
    from starlette.testclient import TestClient
    serve(ctx)
    (app, _, _), = ctx.served

    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/tasks").status_code == 401
        token = (ctx.settings.state_dir / "token").read_text().strip()
        assert client.get("/tasks", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_the_state_dir_is_locked_while_serving(ctx):
    held = []

    def serve_and_probe(app, host, port):
        with pytest.raises(AlreadyRunning):
            with state_lock(ctx.settings.state_dir):
                pass
        held.append(True)

    ctx.serve = serve_and_probe

    assert serve(ctx)[0] == 0 and held == [True]


def test_serve_refuses_when_another_run_holds_the_lock(ctx):
    with state_lock(ctx.settings.state_dir):
        code, out, err = serve(ctx)

    assert code == 1 and "another factory run holds the lock" in err and ctx.served == []


def test_serve_refuses_when_orphans_cannot_be_proved_dead(ctx):
    class Record:
        pid, task_id, stage = 7, "t", "implement"

    def uncertain(directory):
        raise ReapUncertain([], [(Record, "ps: operation not permitted")])

    ctx.reap = uncertain

    code, out, err = serve(ctx)

    assert code == 1 and "cannot prove these processes dead" in err and ctx.served == []


def test_serve_refuses_a_token_file_others_can_read(ctx):
    import os
    serve(ctx)
    os.chmod(ctx.settings.state_dir / "token", 0o644)
    ctx.served.clear()

    code, out, err = serve(ctx)

    assert code == 1 and "readable by others" in err and ctx.served == []


def test_serve_says_which_orphaned_agents_it_killed_before_listening(ctx):
    from factory.process_registry import Record
    ctx.reap = lambda directory: ReapResult([Record(4242, 4242, "t7", "implement", 100.0, 100.5)], [])

    _, out, _ = serve(ctx)

    assert out.splitlines()[0] == "killed orphan of t7 (implement), pid 4242"
    assert out.splitlines()[1].startswith("serving on ")


def test_serve_defaults_to_the_configured_port_and_the_flag_overrides_it(ctx):
    from dataclasses import replace
    ctx.settings = replace(ctx.settings, server_port=9001)

    serve(ctx)
    serve(ctx, "--port", "9002")

    assert [port for _, _, port in ctx.served] == [9001, 9002]


def test_serve_names_an_invalid_database_row_by_its_pid_when_it_discards_it(ctx):
    from factory.cli import _reap_with_the_real_process_table
    from factory.process_registry import Record
    from factory.sqlite_process_records import SqliteProcessRecords
    ctx.reap = _reap_with_the_real_process_table
    SqliteProcessRecords(ctx.settings.db_path).write(Record(1, 1, "t7", "implement", 100.0, 100.5))

    _, out, _ = serve(ctx)

    assert out.splitlines()[0].startswith("discarded invalid process record pid 1: ")
    assert SqliteProcessRecords(ctx.settings.db_path).load() == []
