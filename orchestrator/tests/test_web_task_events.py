import threading
from urllib.parse import unquote

import pytest

from factory.sqlite_db import transaction
from factory.sqlite_store import SqliteTaskStore
from factory.task import Task, TaskState
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


def read_until_end(client, path, stop_after=None):
    """Every event of the stream at `path` until the server ends it; fails instead of hanging.

    With `stop_after`, requests a stop once that many events have arrived."""
    events: list = []
    ended = threading.Event()

    def read():
        with client.stream("GET", path) as response:
            event: list[str] = []
            for line in response.iter_lines():
                if line:
                    event.append(line)
                    continue
                events.append(event)
                event = []
                if len(events) == stop_after:
                    client.app.state.request_stop()
        ended.set()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(TIMEOUT)
    assert ended.is_set(), f"the stream did not end within {TIMEOUT}s; got {events}"
    return events


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
    assert "data: step two</p>" in event


def test_this_tasks_save_sends_the_new_state_as_the_next_event(client, ctx):
    add(ctx, state=TaskState.QUEUED)

    _, (first, second) = read_events(client, EVENTS, count=2, after_first_event=lambda: add(ctx, state=TaskState.FAILED))

    assert any("state: queued" in line for line in first)
    assert second[0] == "event: task"
    assert any("state: failed" in line for line in second)


def test_another_tasks_save_sends_no_event_and_the_next_frame_is_a_keepalive(client, ctx):
    add(ctx)
    client.app.state.keepalive_seconds = 0.2

    _, (first, second) = read_events(client, EVENTS, count=2, after_first_event=lambda: add(ctx, "20260102-999999"))

    assert first[0] == "event: task"
    assert second == [": keepalive"]


def test_a_clean_style_save_without_a_worktree_drops_the_worktree_section(client, ctx):
    add(ctx, state=TaskState.FAILED, worktree="/wt/20260102-030405")

    _, (first, second) = read_events(client, EVENTS, count=2,
                                     after_first_event=lambda: add(ctx, state=TaskState.FAILED, worktree=None))

    assert "data: <p>worktree: /wt/20260102-030405</p>" in first
    assert "data: <p>branch: factory/20260102-030405</p>" in first
    assert second[0] == "event: task"
    assert not any("worktree" in line or "branch" in line for line in second)


def test_a_write_landing_during_the_first_render_still_sends_the_new_state(client, ctx, monkeypatch):
    add(ctx, state=TaskState.QUEUED)
    load, loads = ctx.store.load, []

    def load_then_write(task_id):
        task = load(task_id)
        loads.append(task_id)
        if len(loads) == 2:  # the first render, after the route's own check: write once the old row is read
            add(ctx, state=TaskState.FAILED)
        return task

    monkeypatch.setattr(ctx.store, "load", load_then_write)

    _, (first, second) = read_events(client, EVENTS, count=2)

    assert any("state: queued" in line for line in first)
    assert any("state: failed" in line for line in second)


def test_a_row_corrupted_mid_stream_sends_the_unreadable_fragment_and_keeps_the_stream_open(client, ctx):
    add(ctx)
    client.app.state.keepalive_seconds = 0.2

    def corrupt_then_wake():
        with transaction(ctx.settings.db_path) as conn:
            conn.execute("UPDATE tasks SET data = 'not json' WHERE id = '20260102-030405'")
        add(ctx, "20260102-999999")  # any save wakes the stream; the raw UPDATE does not

    _, (first, *rest) = read_events(client, EVENTS, count=4, after_first_event=corrupt_then_wake)

    unreadable, *_ = [frame for frame in rest if frame != [": keepalive"]]
    assert first[0] == "event: task"
    assert unreadable[0] == "event: task"
    assert any("That task is unreadable." in line for line in unreadable)
    assert not any(text in line for line in unreadable for text in ("not json", "Traceback", "20260102-030405"))
    assert rest[-1] == [": keepalive"]


def test_requesting_a_stop_ends_an_open_task_stream(client, ctx):
    add(ctx)

    events = read_until_end(client, EVENTS, stop_after=1)

    assert [event[0] for event in events] == ["event: task"]


def test_a_task_stream_opened_after_a_stop_ends_at_once(client, ctx):
    add(ctx)
    client.app.state.request_stop()

    assert read_until_end(client, EVENTS) == []


def _save_silently(ctx, task):
    """Save through a store with no listeners, so only some other write can wake the stream."""
    SqliteTaskStore(ctx.settings.db_path).save(task)


def test_a_job_state_write_wakes_the_task_stream(client, ctx):
    add(ctx, state=TaskState.QUEUED)
    jobs = client.app.state.jobs
    job = jobs.create("abandon", "20260102-030405")

    def change_task_unannounced_then_fail_the_job():
        _save_silently(ctx, Task("20260102-030405", "prompt", state=TaskState.FAILED))
        jobs.mark_failed(job.id, "refused")

    _, (first, second) = read_events(client, EVENTS, count=2, after_first_event=change_task_unannounced_then_fail_the_job)

    assert any("state: queued" in line for line in first)
    assert any("state: failed" in line for line in second)


def test_a_refused_job_that_writes_no_task_re_renders_the_fragment_from_the_job_store(client, ctx, monkeypatch):
    add(ctx, state=TaskState.MERGED)
    state = client.app.state
    latest, saw_failed_job = state.jobs.latest_for_task, threading.Event()

    def latest_watched(task_id):
        job = latest(task_id)
        if task_id == "20260102-030405" and job is not None and job.state == "failed":
            saw_failed_job.set()
        return job

    monkeypatch.setattr(state.jobs, "latest_for_task", latest_watched)
    first_event = threading.Event()

    def read():
        with client.stream("GET", EVENTS) as response:
            for line in response.iter_lines():
                if not line:
                    first_event.set()

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    assert first_event.wait(TIMEOUT), "no first event"
    state.actions.enqueue_abandon("20260102-030405")  # a merged task cannot be abandoned: the job fails, no task write

    rendered = saw_failed_job.wait(TIMEOUT)
    state.request_stop()
    reader.join(TIMEOUT)
    state.runner.wait_idle()

    assert rendered, "the stream did not re-render after the refused job failed"
    assert state.jobs.latest_for_task("20260102-030405").state == "failed"
    assert ctx.store.load("20260102-030405").state is TaskState.MERGED


def test_guard_a_job_write_that_changes_nothing_shown_sends_no_event(client, ctx):
    add(ctx)
    client.app.state.keepalive_seconds = 0.2
    jobs = client.app.state.jobs
    job = jobs.create("abandon", "20260102-999999")

    _, (first, second) = read_events(client, EVENTS, count=2, after_first_event=lambda: jobs.mark_failed(job.id, "x"))

    assert first[0] == "event: task"
    assert second == [": keepalive"]
