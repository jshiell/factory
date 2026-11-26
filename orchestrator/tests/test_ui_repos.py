import sqlite3
import threading

import pytest
from starlette.testclient import TestClient

from factory.repos import RepoRegistry
from factory.server import _build_repos_app
from factory.task import Task, TaskState
from test_server_repos import make_ctx


@pytest.fixture
def contexts(tmp_path):
    return {name: make_ctx(tmp_path, name) for name in ("a", "b")}


@pytest.fixture
def client(contexts):
    with TestClient(_build_repos_app(RepoRegistry(contexts, {})), base_url="http://127.0.0.1:8765") as client:
        yield client


def test_the_index_lists_the_tasks_of_every_repo_each_linked_under_its_repo(client, contexts):
    contexts["a"].store.save(Task(id="in-a", prompt="goal a", state=TaskState.QUEUED))
    contexts["b"].store.save(Task(id="in-b", prompt="goal b", state=TaskState.QUEUED))

    page = client.get("/ui/")

    assert page.status_code == 200
    assert 'href="/ui/repos/a/tasks/in-a"' in page.text and "goal a" in page.text
    assert 'href="/ui/repos/b/tasks/in-b"' in page.text and "goal b" in page.text


def test_a_repo_whose_store_cannot_be_read_is_named_and_the_others_still_show(client, contexts, monkeypatch):
    contexts["a"].store.save(Task(id="in-a", prompt="goal a", state=TaskState.QUEUED))

    def unreadable():
        raise sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr(contexts["b"].store, "list", unreadable)

    page = client.get("/ui/")

    assert page.status_code == 200
    assert "in-a" in page.text
    assert "b: unreadable" in page.text


def test_a_task_page_shows_the_task_of_its_own_repo_and_links_to_its_events(client, contexts):
    contexts["a"].store.save(Task(id="same", prompt="goal in a", state=TaskState.QUEUED))
    contexts["b"].store.save(Task(id="same", prompt="goal in b", state=TaskState.QUEUED))

    page = client.get("/ui/repos/a/tasks/same")

    assert page.status_code == 200
    assert "goal in a" in page.text and "goal in b" not in page.text
    assert 'sse-connect="/ui/repos/a/tasks/same/events"' in page.text


def first_event(client, path):
    """The lines of the first SSE event of `path`; fails instead of hanging."""
    lines: list[str] = []

    def read():
        with client.stream("GET", path) as response:
            for line in response.iter_lines():
                if not line:
                    return
                lines.append(line)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(10)
    assert not reader.is_alive(), f"no event within 10s for {path}"
    return lines


def test_a_task_stream_sends_the_task_of_its_own_repo(client, contexts):
    contexts["a"].store.save(Task(id="same", prompt="goal in a", state=TaskState.QUEUED))
    contexts["b"].store.save(Task(id="same", prompt="goal in b", state=TaskState.QUEUED))

    event = "\n".join(first_event(client, "/ui/repos/a/tasks/same/events"))

    assert event.startswith("event: task")
    assert "goal in a" in event and "goal in b" not in event
