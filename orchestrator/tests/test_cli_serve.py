import io
from dataclasses import replace

import pytest
from starlette.testclient import TestClient

from factory.cli import IO, main
from factory.lock import AlreadyRunning, state_lock
from factory.process_registry import ReapResult, ReapUncertain
from servecli import make_server


@pytest.fixture
def ctx(tmp_path):
    return make_server(tmp_path, ("a", "b"), skipped={"old": "bad config"})


def serve(ctx, *argv):
    out, err = io.StringIO(), io.StringIO()
    code = main(["serve", *argv], IO(io.StringIO(), out, err), ctx)
    return code, out.getvalue(), err.getvalue()


def a_state_dir(ctx):
    return ctx.contexts["a"].settings.state_dir


def test_serve_listens_on_loopback_port_8765_by_default_and_says_where(ctx):
    code, out, err = serve(ctx)

    assert code == 0
    (_, host, port), = ctx.served
    assert (host, port) == ("127.0.0.1", 8765)
    token_file = ctx.settings.home / "_server" / "token"
    assert out.strip() == f"serving on http://127.0.0.1:8765; token in {token_file}"
    assert token_file.read_text().strip() not in out


def test_serve_serves_every_repo_and_names_the_ones_it_skipped(ctx):
    code, _, err = serve(ctx)

    (app, _, _), = ctx.served
    token = (ctx.settings.home / "_server" / "token").read_text().strip()
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/repos").status_code == 401
        listed = client.get("/repos", headers={"Authorization": f"Bearer {token}"}).json()
    assert listed == {"repos": ["a", "b"], "skipped": {"old": "bad config"}}
    assert err == "skipped repo old: bad config\n"


def test_serve_warns_that_an_old_token_in_a_state_dir_is_unused(ctx):
    old_token = a_state_dir(ctx) / "token"
    old_token.write_text("old")

    code, _, err = serve(ctx)

    assert code == 0
    assert f"warning: {old_token} is unused; the token is now in {ctx.settings.token_path}" in err.splitlines()


def test_serve_does_not_warn_without_an_old_token(tmp_path):
    ctx = make_server(tmp_path)

    assert serve(ctx)[2] == ""


def test_serve_takes_a_port(ctx):
    serve(ctx, "--port", "0")

    assert ctx.served[0][2] == 0


def test_the_state_dirs_are_locked_while_serving(ctx):
    held = []

    def serve_and_probe(app, host, port):
        for name in ("a", "b"):
            with pytest.raises(AlreadyRunning), state_lock(ctx.contexts[name].settings.state_dir):
                pass
        with pytest.raises(AlreadyRunning), state_lock(ctx.settings.server_dir):
            pass
        held.append(True)

    ctx.serve = serve_and_probe

    assert serve(ctx)[0] == 0 and held == [True]


def test_serve_refuses_when_another_server_holds_the_server_lock(ctx):
    with state_lock(ctx.settings.server_dir):
        code, out, err = serve(ctx)

    assert code == 1 and "another factory server holds the lock" in err and ctx.served == []


def test_a_repo_another_run_holds_is_skipped_and_the_others_are_served(ctx):
    with state_lock(a_state_dir(ctx)):
        code, out, err = serve(ctx)

    assert code == 0
    assert "skipped repo a: another factory run holds the lock" in err
    (app, _, _), = ctx.served
    assert app.state.registry.started_names == ("b",)


def test_a_repo_whose_orphans_cannot_be_proved_dead_is_skipped(ctx):
    class Record:
        pid, task_id, stage = 7, "t", "implement"

    def uncertain(records):
        raise ReapUncertain([], [(Record, "ps: operation not permitted")])

    ctx.contexts["a"].reap = uncertain

    code, out, err = serve(ctx)

    assert code == 0 and "skipped repo a: " in err and "cannot prove these processes dead" in err
    assert ctx.served[0][0].state.registry.started_names == ("b",)


def test_serve_refuses_a_token_file_others_can_read(ctx):
    import os
    serve(ctx)
    os.chmod(ctx.settings.token_path, 0o644)
    ctx.served.clear()

    code, out, err = serve(ctx)

    assert code == 1 and "readable by others" in err and ctx.served == []


def test_serve_says_which_orphaned_agents_it_killed_in_which_repo_before_listening(ctx):
    from factory.process_registry import Record
    ctx.contexts["b"].reap = lambda records: ReapResult([Record(4242, 4242, "t7", "implement", 100.0, 100.5)], [])

    _, out, _ = serve(ctx)

    assert out.splitlines()[0] == "b: killed orphan of t7 (implement), pid 4242"
    assert out.splitlines()[1].startswith("serving on ")


def test_serve_defaults_to_the_configured_port_and_the_flag_overrides_it(ctx):
    ctx.settings = replace(ctx.settings, server_port=9001)

    serve(ctx)
    serve(ctx, "--port", "9002")

    assert [port for _, _, port in ctx.served] == [9001, 9002]


def test_serve_names_an_invalid_database_row_by_its_pid_when_it_discards_it(ctx):
    from factory.cli import _reap_with_the_real_process_table
    from factory.process_registry import Record
    from factory.sqlite_process_records import SqliteProcessRecords
    ctx.contexts["a"].reap = _reap_with_the_real_process_table
    db_path = ctx.contexts["a"].settings.db_path
    SqliteProcessRecords(db_path).write(Record(1, 1, "t7", "implement", 100.0, 100.5))

    _, out, _ = serve(ctx)

    assert out.splitlines()[0].startswith("a: discarded invalid process record pid 1: ")
    assert SqliteProcessRecords(db_path).load() == []
