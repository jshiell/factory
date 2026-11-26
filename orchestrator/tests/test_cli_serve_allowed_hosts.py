"""`factory serve` with `server_allowed_hosts` set warns once, naming the hosts and the plain-HTTP risk."""
import io
from dataclasses import replace

import pytest

from factory.cli import IO, Context, main
from factory.process_registry import ReapResult
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore

PLAIN_HTTP = ("the connection is plain HTTP, so the token, login code and session cookie can be read on the way: "
              "use a VPN or SSH tunnel")

WARNINGS = {
    "0.0.0.0": "warning: bound to 0.0.0.0, so port 9001 is reachable from the network and accepts the Host names "
               f"nas.lan, 192.168.1.5 besides loopback; {PLAIN_HTTP}\n",
    "127.0.0.1": "warning: bound to 127.0.0.1 port 9001, accepting only loopback and the Host names "
                 f"nas.lan, 192.168.1.5 (for a tunnel that keeps the Host); {PLAIN_HTTP}\n",
}


@pytest.fixture
def ctx(tmp_path):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt", home=tmp_path / "home",
                        server_allowed_hosts=("nas.lan", "192.168.1.5"))
    settings.state_dir.mkdir()
    context = Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={})
    context.reap = lambda directory: ReapResult([], [])
    return context


@pytest.mark.parametrize("bind", ["0.0.0.0", "127.0.0.1"])
def test_a_set_list_prints_one_warning_naming_the_hosts_before_listening(ctx, bind):
    out, err = io.StringIO(), io.StringIO()
    heard_before_listening = []
    ctx.serve = lambda app, host, port: heard_before_listening.append(err.getvalue())
    ctx.settings = replace(ctx.settings, server_host=bind)

    code = main(["serve", "--port", "9001"], IO(io.StringIO(), out, err), ctx)

    assert code == 0
    assert err.getvalue() == WARNINGS[bind]
    assert heard_before_listening == [WARNINGS[bind]]
    assert "still refused" not in err.getvalue()
    assert out.getvalue().splitlines() == [f"serving on http://127.0.0.1:9001; token in {ctx.settings.token_path}"]
