import re
import sqlite3
import threading

import pytest
from starlette.testclient import TestClient

from factory.clean_tasks import Candidate
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


def _reviewed(task_id, prompt):
    return Task(id=task_id, prompt=prompt, state=TaskState.REVIEWED, plan="the plan")


def test_the_task_page_and_its_stream_link_the_actions_under_their_repo(client, contexts):
    contexts["a"].store.save(_reviewed("same", "goal in a"))  # guard: covered by the template change below

    page = client.get("/ui/repos/a/tasks/same")
    event = "\n".join(first_event(client, "/ui/repos/a/tasks/same/events"))

    for text in (page.text, event):
        assert 'href="/ui/repos/a/tasks/same/approve"' in text
        assert 'href="/ui/repos/a/tasks/same/reject"' in text


def test_the_new_task_page_offers_every_repo_and_creates_the_task_in_the_chosen_one(client, contexts):
    page = client.get("/ui/tasks/new")
    done = client.post("/ui/tasks/new", data={"repo": "b", "goal": "do it"}, follow_redirects=False)

    assert '<option value="a"' in page.text and '<option value="b"' in page.text
    [task] = contexts["b"].store.list().tasks
    assert contexts["a"].store.list().tasks == []
    assert done.status_code == 303 and done.headers["location"] == f"/ui/repos/b/tasks/{task.id}"


def test_a_new_task_for_an_unknown_repo_is_refused_and_creates_nothing(client, contexts):
    done = client.post("/ui/tasks/new", data={"repo": "nope", "goal": "do it"}, follow_redirects=False)

    assert done.status_code == 422
    assert contexts["a"].store.list().tasks == [] and contexts["b"].store.list().tasks == []


def test_a_blank_goal_keeps_the_chosen_repo_selected_on_the_form(client):
    done = client.post("/ui/tasks/new", data={"repo": "b", "goal": "  "})

    assert done.status_code == 422
    assert '<option value="b" selected' in done.text


def test_the_jobs_page_lists_the_jobs_of_every_repo_linked_under_its_repo(client):
    client.app.state.repos["a"].jobs.create("advance", "in-a")
    client.app.state.repos["b"].jobs.create("merge", "in-b")

    page = client.get("/ui/jobs")

    assert page.status_code == 200
    assert "<h2>a</h2>" in page.text and "<h2>b</h2>" in page.text
    assert 'href="/ui/repos/a/tasks/in-a"' in page.text and "Advance" in page.text
    assert 'href="/ui/repos/b/tasks/in-b"' in page.text and "Merge" in page.text


def test_a_repo_whose_jobs_cannot_be_read_is_named_and_the_others_still_show(client, monkeypatch):
    client.app.state.repos["a"].jobs.create("advance", "in-a")

    def unreadable():
        raise sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr(client.app.state.repos["b"].jobs, "list", unreadable)

    page = client.get("/ui/jobs")

    assert page.status_code == 200
    assert "in-a" in page.text
    assert "b: unreadable" in page.text


@pytest.fixture
def clean_candidates(monkeypatch):
    found = [Candidate(Task(id="old-task", prompt="", state=TaskState.FAILED, worktree="/w/t1"),
                       directory_exists=True, dirty=True, commits_ahead=2, branch_exists=True)]
    monkeypatch.setattr("factory.server.clean_candidates", lambda *a, **k: found)
    return found


def test_the_clean_page_of_a_repo_links_its_tasks_and_confirming_queues_the_job_in_that_repo(client, clean_candidates):
    page = client.get("/ui/repos/a/clean")
    token = re.search(r'name="token" value="([0-9a-f]{64})"', page.text).group(1)

    done = client.post("/ui/repos/a/clean", data={"token": token}, follow_redirects=False)

    assert page.status_code == 200
    assert 'href="/ui/repos/a/tasks/old-task"' in page.text and 'action="/ui/repos/a/clean"' in page.text
    assert done.status_code == 303 and done.headers["location"] == "/ui/jobs"
    assert [job.kind for job in client.app.state.repos["a"].jobs.list()] == ["clean"]
    assert client.app.state.repos["b"].jobs.list() == []


def test_the_index_links_each_repos_clean_page(client):
    page = client.get("/ui/")

    assert 'href="/ui/repos/a/clean"' in page.text and 'href="/ui/repos/b/clean"' in page.text


def test_the_costs_page_reports_each_repo_apart_with_task_links_under_its_repo(client, contexts):
    contexts["a"].ledger.append("in-a", 1.25, stage="plan")
    contexts["b"].ledger.append("in-b", 2.5, stage="build")

    page = client.get("/ui/costs")

    assert page.status_code == 200
    assert "<h2>a</h2>" in page.text and "<h2>b</h2>" in page.text
    assert "Total: $1.25" in page.text and "Total: $2.50" in page.text
    assert 'href="/ui/repos/a/tasks/in-a"' in page.text and 'href="/ui/repos/b/tasks/in-b"' in page.text


def test_a_repo_whose_ledger_cannot_be_read_is_named_and_the_others_still_show(client, contexts, monkeypatch):
    contexts["a"].ledger.append("in-a", 1.25, stage="plan")

    def unreadable():
        raise sqlite3.DatabaseError("file is not a database")

    monkeypatch.setattr(contexts["b"].ledger, "entries", unreadable)

    page = client.get("/ui/costs")

    assert page.status_code == 200
    assert "in-a" in page.text
    assert "b: unreadable" in page.text


def test_the_index_streams_the_tasks_of_every_repo_and_again_when_any_repo_changes(client, contexts):
    contexts["a"].store.save(Task(id="in-a", prompt="goal a", state=TaskState.QUEUED))
    events: list[str] = []
    first, changed = threading.Event(), threading.Event()

    def read():
        block: list[str] = []
        with client.stream("GET", "/ui/events") as response:
            for line in response.iter_lines():
                if line:
                    block.append(line)
                    continue
                events.append("\n".join(block))
                block = []
                first.set()
                if "in-b" in events[-1]:
                    changed.set()
                    return

    threading.Thread(target=read, daemon=True).start()
    assert first.wait(10), "no event within 10s"
    contexts["b"].store.save(Task(id="in-b", prompt="goal b", state=TaskState.QUEUED))

    assert changed.wait(10), "no event after a task was saved in the other repo"
    assert events[0].startswith("event: repos") and "in-a" in events[0] and "in-b" not in events[0]
    assert 'href="/ui/repos/b/tasks/in-b"' in events[-1] and 'href="/ui/repos/a/tasks/in-a"' in events[-1]


def test_the_index_page_connects_to_the_stream(client):
    assert 'sse-connect="/ui/events"' in client.get("/ui/").text


def test_rejecting_a_plan_acts_on_the_repo_in_the_url_and_returns_to_its_task_page(client, contexts):
    contexts["a"].store.save(_reviewed("same", "goal in a"))
    contexts["b"].store.save(_reviewed("same", "goal in b"))

    page = client.get("/ui/repos/a/tasks/same/reject")
    done = client.post("/ui/repos/a/tasks/same/reject", follow_redirects=False)

    assert 'action="/ui/repos/a/tasks/same/reject"' in page.text
    assert 'href="/ui/repos/a/tasks/same"' in page.text
    assert done.status_code == 303 and done.headers["location"] == "/ui/repos/a/tasks/same"
    assert contexts["a"].store.load("same").state is not TaskState.REVIEWED
    assert contexts["b"].store.load("same").state is TaskState.REVIEWED


def test_a_login_code_gives_a_session_and_the_static_files_are_served(client):
    code = client.app.state.login_codes.issue()

    done = client.get(f"/ui/login?code={code}")

    assert done.status_code == 200 and "set-cookie" in done.headers
    assert client.get("/ui/login?code=used").status_code == 401
    assert client.get("/ui/static/ui.css").status_code == 200
