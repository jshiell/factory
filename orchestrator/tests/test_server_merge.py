from dataclasses import replace
from datetime import datetime

import pytest
from starlette.testclient import TestClient

from gitutil import git

from factory.cli import Context
from factory.server import create_app
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore
from factory.task import Task, TaskState
from factory.worktrees import WorktreeManager


@pytest.fixture
def ctx(tmp_path, repo):
    settings = Settings(repo=repo, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt")
    settings.state_dir.mkdir()
    return Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={},
                   clock=lambda: datetime(2026, 1, 2, 3, 4, 5))


@pytest.fixture
def audited(ctx, repo):
    worktree, base = WorktreeManager(repo, ctx.settings.worktree_root).create("t1")
    (worktree / "b.txt").write_text("b")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-q", "-m", "Task t1")
    task = Task(id="t1", prompt="p", state=TaskState.AUDITED, worktree=str(worktree), base_sha=base,
                audited_sha=git(worktree, "rev-parse", "HEAD"), audit_findings=("looks fine",))
    ctx.store.save(task)
    return task


@pytest.fixture
def client(ctx):
    with TestClient(create_app(ctx)) as client:
        yield client


def test_merge_preview_shows_branch_diffstat_findings_and_the_audited_sha(client, audited):
    response = client.get("/tasks/t1/merge-preview")

    body = response.json()
    assert response.status_code == 200
    assert body["branch"] == "factory/t1"
    assert body["audited_sha"] == audited.audited_sha
    assert "b.txt" in body["diffstat"]
    assert body["findings"] == ["looks fine"]


def test_merge_preview_of_a_task_that_is_not_audited_is_409(client, ctx, audited):
    ctx.store.save(replace(audited, state=TaskState.VERIFIED))

    response = client.get("/tasks/t1/merge-preview")

    assert response.status_code == 409
    assert response.json()["error"] == "merge refused"


def test_merge_preview_of_an_unknown_task_is_404(client):
    assert client.get("/tasks/nope/merge-preview").status_code == 404


def _wait(client, job_id):
    client.app.state.runner.wait_idle()
    return client.get(f"/jobs/{job_id}").json()


def test_merge_with_the_shown_sha_fast_forwards_main_and_records_merged(client, ctx, audited, repo):
    response = client.post("/tasks/t1/merge", json={"audited_sha": audited.audited_sha})

    assert response.status_code == 202
    job = _wait(client, response.json()["job"]["id"])
    assert job["state"] == "succeeded"
    assert ctx.store.load("t1").state is TaskState.MERGED
    assert git(repo, "rev-parse", "HEAD") == audited.audited_sha


def test_merge_with_a_stale_sha_is_409_and_changes_nothing(client, ctx, audited, repo):
    response = client.post("/tasks/t1/merge", json={"audited_sha": "0" * 40})

    assert response.status_code == 409
    assert response.json()["error"] == "merge refused"
    assert ctx.store.load("t1").state is TaskState.AUDITED
    assert git(repo, "rev-parse", "HEAD") == audited.base_sha


def test_merge_without_audited_sha_is_422(client, audited):
    assert client.post("/tasks/t1/merge", json={}).status_code == 422


def test_merge_of_an_unknown_task_is_404(client):
    assert client.post("/tasks/nope/merge", json={"audited_sha": "x"}).status_code == 404


def test_a_merge_that_main_outran_ends_the_job_failed_and_changes_nothing(client, ctx, audited, repo):
    (repo / "c.txt").write_text("c")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "main moved")

    job = _wait(client, client.post("/tasks/t1/merge", json={"audited_sha": audited.audited_sha}).json()["job"]["id"])

    assert job["state"] == "failed"
    assert job["error"].startswith("merge refused: ")
    assert ctx.store.load("t1").state is TaskState.AUDITED
