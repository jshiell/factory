from datetime import datetime

import pytest
from starlette.testclient import TestClient

from factory.cli import Context
from factory.repos import RepoRegistry
from factory.server import _build_repos_app
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore


def make_ctx(home, name):
    project = home / name
    settings = Settings(repo=project / "repo", state_dir=project / "state", worktree_root=project / "worktrees", home=home)
    settings.state_dir.mkdir(parents=True)
    return Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={},
                   clock=lambda: datetime(2026, 1, 2, 3, 4, 5))


@pytest.fixture
def contexts(tmp_path):
    return {name: make_ctx(tmp_path, name) for name in ("a", "b")}


@pytest.fixture
def client(contexts):
    with TestClient(_build_repos_app(RepoRegistry(contexts, {"busy": "another factory run holds the lock"})), base_url="http://127.0.0.1:8765") as client:
        yield client


def test_each_repo_serves_its_own_tasks_under_its_prefix(client, contexts):
    created = client.post("/repos/a/tasks", json={"goal": "in a"})

    assert created.status_code == 201
    assert [t["id"] for t in client.get("/repos/a/tasks").json()["tasks"]] == [created.json()["id"]]
    assert client.get("/repos/b/tasks").json()["tasks"] == []
    assert contexts["b"].store.list().tasks == []


def test_a_job_is_recorded_in_the_store_of_the_repo_it_was_queued_in(client):  # guard: the bound runner covers it
    task_id = client.post("/repos/a/tasks", json={"goal": "in a"}).json()["id"]

    client.post(f"/repos/a/tasks/{task_id}/advance")

    assert len(client.get("/repos/a/jobs").json()["jobs"]) == 1
    assert client.get("/repos/b/jobs").json()["jobs"] == []


def test_the_repo_list_names_started_repos_and_says_why_others_were_skipped(client):
    assert client.get("/repos").json() == {"repos": ["a", "b"], "skipped": {"busy": "another factory run holds the lock"}}


@pytest.mark.parametrize("path", ["/repos/nope/tasks", "/repos/nope/tasks/x/advance", "/repos/nope"])
def test_an_unknown_repo_gets_a_json_404_naming_the_known_repos(client, path):
    response = client.get(path)

    assert response.status_code == 404
    assert response.json() == {"error": "unknown repo", "detail": "unknown repo nope; known: a, b"}


def test_a_skipped_repo_gets_a_json_404_saying_why(client):
    response = client.get("/repos/busy/tasks")

    assert response.status_code == 404
    assert response.json()["detail"] == "repo busy was skipped: another factory run holds the lock"
