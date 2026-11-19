import select
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx2
import pytest

from factory.lock import state_lock
from test_cli_wiring import _free_port, project  # noqa: F401  (project is a fixture)

FACTORY = Path(sys.executable).parent / "factory"  # the script itself, so the signal reaches serve and not a wrapper
START_SECONDS = 30
EXIT_SECONDS = 15  # well past the 5 s graceful-shutdown backstop


def _start_serve(repo: Path, env, port: int) -> subprocess.Popen:
    server = subprocess.Popen([str(FACTORY), "serve", "--port", str(port)], cwd=repo, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    readable, _, _ = select.select([server.stdout], [], [], START_SECONDS)
    announced = server.stdout.readline() if readable else ""
    assert announced.startswith("serving on "), announced
    return server


def _log_in(client: httpx2.Client, token: str) -> None:
    """Get a UI session cookie, once uvicorn accepts connections (it binds after the announcement)."""
    deadline = time.monotonic() + START_SECONDS
    while True:
        try:
            issued = client.post("/ui-login-codes", headers={"Authorization": f"Bearer {token}"})
            break
        except httpx2.TransportError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)
    assert issued.status_code == 200, issued.text
    assert client.get(issued.json()["url"]).status_code == 200


def _first_event(lines) -> list[str]:
    event = []
    for line in lines:
        if not line:
            return event
        event.append(line)
    return event


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP], ids=["SIGTERM", "SIGHUP"])
def test_a_signal_stops_serve_with_an_event_stream_open_and_releases_the_lock(project, signum):  # noqa: F811
    repo, env, tmp_path = project
    state_dir = tmp_path / "state"
    port = _free_port()
    server = _start_serve(repo, env, port)
    try:
        with httpx2.Client(base_url=f"http://127.0.0.1:{port}", timeout=START_SECONDS) as client:
            _log_in(client, (state_dir / "token").read_text().strip())
            with client.stream("GET", "/ui/events") as stream:
                lines = stream.iter_lines()  # held: a discarded iterator closes the stream when collected
                assert _first_event(lines)[0] == "event: tasks"

                server.send_signal(signum)
                # a clean end of the chunked body, not the backstop cancelling the stream mid-response
                assert all(line in ("", ": keepalive") for line in lines)
                try:
                    server.wait(timeout=EXIT_SECONDS)
                except subprocess.TimeoutExpired:
                    pytest.fail(f"serve did not exit within {EXIT_SECONDS}s of {signum.name} with an event stream open")
    finally:
        if server.poll() is None:
            server.kill()
        server.wait(timeout=START_SECONDS)
        server.stdout.close()
        server.stderr.close()

    with state_lock(state_dir):
        pass
