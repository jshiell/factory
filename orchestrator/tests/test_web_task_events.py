import threading
from urllib.parse import unquote

import pytest

from factory.sqlite_db import transaction
from webhelpers import add, assert_ui_security_headers

pytest_plugins = ["webfixtures"]

TIMEOUT = 5
EVENTS = "/ui/tasks/20260102-030405/events"


def read_events(client, path, count=1, after_first_event=None):
    """The first `count` events (each a list of lines) of the stream at `path`; fails instead of hanging.

    `after_first_event` runs once the first event has arrived, to cause the next one."""
    seen: list = []

    def read():
        with client.stream("GET", path) as response:
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


def fetch_whole(client, path):
    """The full response at `path`, read off the test thread so a stream that never ends fails instead of hanging."""
    got: list = []

    def read():
        with client.stream("GET", path) as response:
            response.read()
            got.append(response)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(TIMEOUT)
    assert got, f"{path} gave no complete response within {TIMEOUT}s"
    return got[0]


@pytest.fixture
def broken(ctx):
    with transaction(ctx.settings.db_path) as conn:
        conn.execute("INSERT INTO tasks (id, state, data) VALUES ('broken-one', 'queued', 'not json')")


@pytest.mark.parametrize("task_id, status, text", [
    ("nothing-here", 404, "does not exist"), ("a.b", 404, "does not exist"), ("a%20b", 404, "does not exist"),
    ("%3Cscript%3E", 404, "does not exist"), ("broken-one", 500, "unreadable")])
def test_the_task_stream_refuses_a_missing_invalid_or_unreadable_id_with_an_html_page(client, broken, task_id, status, text):
    response = fetch_whole(client, f"/ui/tasks/{task_id}/events")

    assert response.status_code == status
    assert response.headers["content-type"].startswith("text/html")
    assert text in response.text
    assert 'href="/ui/"' in response.text
    assert "Traceback" not in response.text and "not json" not in response.text
    for echoed in (task_id, unquote(task_id), "&lt;script"):
        assert echoed not in response.text
    assert_ui_security_headers(response)


def stream_headers(client, path):
    """The status and headers of the stream at `path`, taken without waiting for it to end."""
    got: list = []

    def read():
        with client.stream("GET", path) as response:
            got.append(response)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(TIMEOUT)
    assert got, f"{path} gave no response within {TIMEOUT}s"
    return got[0]


def test_guard_the_task_stream_carries_the_security_headers(client, ctx):
    add(ctx)

    response = stream_headers(client, EVENTS)

    assert response.status_code == 200
    assert_ui_security_headers(response)


def test_guard_the_task_stream_without_a_session_is_401(client, ctx):
    add(ctx)
    client.cookies.clear()

    assert fetch_whole(client, EVENTS).status_code == 401
    assert fetch_whole(client, "/ui/tasks/nothing-here/events").status_code == 401


def test_the_task_stream_first_event_is_the_task_fragment(client, ctx):
    add(ctx, goal="first goal", plan="step one\nstep two")

    (status, content_type), (event, *_) = read_events(client, EVENTS)

    assert status == 200
    assert content_type.startswith("text/event-stream")
    assert event[0] == "event: task"
    assert all(line.startswith("data: ") for line in event[1:])
    assert any("first goal" in line for line in event[1:])
    assert "data: step two</pre>" in event
