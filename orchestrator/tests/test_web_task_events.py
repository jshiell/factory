import threading

from webhelpers import add

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


def test_the_task_stream_first_event_is_the_task_fragment(client, ctx):
    add(ctx, goal="first goal", plan="step one\nstep two")

    (status, content_type), (event, *_) = read_events(client, EVENTS)

    assert status == 200
    assert content_type.startswith("text/event-stream")
    assert event[0] == "event: task"
    assert all(line.startswith("data: ") for line in event[1:])
    assert any("first goal" in line for line in event[1:])
    assert "data: step two</pre>" in event
