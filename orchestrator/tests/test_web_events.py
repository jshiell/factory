import threading
from datetime import datetime

import pytest
from starlette.testclient import TestClient

from factory.cli import Context
from factory.server import create_secured_app, load_or_create_token
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore
from factory.task import Task, TaskState

TIMEOUT = 5


@pytest.fixture
def ctx(tmp_path):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt")
    settings.state_dir.mkdir()
    return Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={},
                   clock=lambda: datetime(2026, 1, 2, 3, 4, 5))


@pytest.fixture
def client(ctx):
    token = load_or_create_token(ctx.settings.state_dir)
    with TestClient(create_secured_app(ctx, token), base_url="http://127.0.0.1:8765") as client:
        client.get("/ui/login", params={"code": client.app.state.login_codes.issue()})
        yield client


def read_events(client, count=1, after_first_event=None):
    """The first `count` events (each a list of lines) of /ui/events; fails instead of hanging.

    `after_first_event` runs once the first event has arrived, to cause the next one."""
    seen: list = []

    def read():
        with client.stream("GET", "/ui/events") as response:
            seen.append((response.status_code, response.headers["content-type"]))
            event: list[str] = []
            for line in response.iter_lines():
                if line:
                    event.append(line)
                    continue
                seen.append(event)
                event = []
                if len(seen) > count:
                    return
                if after_first_event and len(seen) == 2:
                    after_first_event()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(TIMEOUT)
    assert not reader.is_alive(), f"no {count} event(s) within {TIMEOUT}s; got {seen}"
    return seen[0], seen[1:]


def test_events_answers_an_event_stream_whose_first_event_is_the_task_table(client, ctx):
    ctx.store.save(Task("20260102-030405", "p", state=TaskState.QUEUED, goal="first goal"))

    (status, content_type), (event, *_) = read_events(client)

    assert status == 200
    assert content_type.startswith("text/event-stream")
    assert event[0] == "event: tasks"
    assert all(line.startswith("data: ") for line in event[1:])
    assert any("first goal" in line for line in event[1:])


def test_carriage_returns_in_task_text_neither_add_nor_end_an_event(client, ctx):
    ctx.store.save(Task("20260102-030405", "p", state=TaskState.QUEUED, goal="one\rtwo\r\nthree"))

    _, (event, *_) = read_events(client)

    assert event[0] == "event: tasks"
    assert all(line.startswith("data: ") for line in event[1:])


def test_a_store_save_sends_the_new_state_as_the_next_event(client, ctx):
    ctx.store.save(Task("20260102-030405", "p", state=TaskState.QUEUED, goal="g"))

    _, (first, second) = read_events(client, count=2, after_first_event=lambda: ctx.store.save(
        Task("20260102-030405", "p", state=TaskState.FAILED, goal="g")))

    assert any("queued" in line for line in first)
    assert any("failed" in line for line in second)
    assert not any("failed" in line for line in first)


def test_guard_events_needs_the_session_cookie(client):
    client.cookies.clear()

    assert client.get("/ui/events").status_code == 401
