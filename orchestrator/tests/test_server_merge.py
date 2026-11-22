import shutil
import subprocess
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from factory.cli import Context
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore
from factory.task import Task, TaskState
from factory.worktrees import WorktreeManager
from gitutil import git
from serving import served


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
    with served(ctx) as client:
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


def test_merge_preview_on_another_branch_than_main_branch_is_409(client, ctx, audited, repo):
    ctx.settings = replace(ctx.settings, main_branch="main")
    git(repo, "checkout", "-q", "-b", "other")

    response = client.get("/tasks/t1/merge-preview")

    assert response.status_code == 409
    assert response.json()["error"] == "merge refused"
    assert "main is on branch other, not main" in response.json()["detail"]


MERGE_CHECK_SENTENCE = "Git could not check this task's branch and worktree, so it cannot be merged."


def test_a_git_failure_in_merge_preview_is_409_with_a_fixed_sentence_and_no_git_command(client, ctx, tmp_path):
    ctx.store.save(Task(id="t2", prompt="p", state=TaskState.AUDITED, worktree=str(tmp_path),
                        base_sha="0" * 40, audited_sha="1" * 40))  # no factory/t2 branch: rev-parse fails

    response = client.get("/tasks/t2/merge-preview")

    assert response.status_code == 409
    assert response.json() == {"error": "merge check failed", "detail": MERGE_CHECK_SENTENCE}
    assert "rev-parse" not in response.text


def test_a_missing_worktree_in_merge_preview_is_409_with_a_fixed_sentence_and_no_path(client, audited):
    shutil.rmtree(audited.worktree)

    response = client.get("/tasks/t1/merge-preview")

    assert response.status_code == 409
    assert response.json() == {"error": "merge check failed", "detail": MERGE_CHECK_SENTENCE}
    assert audited.worktree not in response.text


def test_an_audited_task_with_no_worktree_in_merge_preview_is_409_merge_check_failed(client, ctx, audited):
    ctx.store.save(replace(audited, worktree=None))

    response = client.get("/tasks/t1/merge-preview")

    assert response.status_code == 409
    assert response.json() == {"error": "merge check failed", "detail": MERGE_CHECK_SENTENCE}


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


def test_a_merge_that_crashed_after_the_fast_forward_can_be_previewed_and_finished(client, ctx, audited, repo):
    git(repo, "merge", "-q", "--ff-only", audited.audited_sha)

    assert client.get("/tasks/t1/merge-preview").status_code == 200
    response = client.post("/tasks/t1/merge", json={"audited_sha": audited.audited_sha})

    job = _wait(client, response.json()["job"]["id"])
    assert job["state"] == "succeeded"
    assert ctx.store.load("t1").state is TaskState.MERGED


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


def test_a_merge_on_another_branch_than_main_branch_ends_the_job_failed_and_changes_nothing(client, ctx, audited, repo):
    ctx.settings = replace(ctx.settings, main_branch="main")
    git(repo, "checkout", "-q", "-b", "other")

    job = _wait(client, client.post("/tasks/t1/merge", json={"audited_sha": audited.audited_sha}).json()["job"]["id"])

    assert job["state"] == "failed"
    assert job["error"] == "merge refused: main is on branch other, not main (main_branch in factory.toml)"
    assert git(repo, "rev-parse", "HEAD") == audited.base_sha
    assert ctx.store.load("t1").state is TaskState.AUDITED


def test_a_rebase_on_another_branch_than_main_branch_ends_the_job_failed_and_changes_nothing(client, ctx, audited, repo):
    ctx.settings = replace(ctx.settings, main_branch="main")
    (repo / "c.txt").write_text("c")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "main moved")
    git(repo, "checkout", "-q", "-b", "other")

    job = _wait(client, client.post("/tasks/t1/rebase").json()["job"]["id"])

    assert job["state"] == "failed"
    assert job["error"] == "rebase refused: main is on branch other, not main (main_branch in factory.toml)"
    assert ctx.store.load("t1") == audited


JOB_FAILED_SENTENCE = "Git or the system failed while running this job. The server log has the details."


def test_a_git_failure_in_a_merge_job_stores_the_fixed_sentence_not_the_command(client, audited, monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(128, ["git", "merge", "secret-ref"])

    monkeypatch.setattr("factory.server.merge_task", fail)

    job = _wait(client, client.post("/tasks/t1/merge", json={"audited_sha": audited.audited_sha}).json()["job"]["id"])

    assert (job["state"], job["error"]) == ("failed", JOB_FAILED_SENTENCE)


def test_a_git_failure_in_a_rebase_job_stores_the_fixed_sentence_not_the_command(client, audited, monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(128, ["git", "rebase", "secret-ref"])

    monkeypatch.setattr("factory.server.rebase_task", fail)

    job = _wait(client, client.post("/tasks/t1/rebase").json()["job"]["id"])

    assert (job["state"], job["error"]) == ("failed", JOB_FAILED_SENTENCE)


@pytest.fixture
def failed(ctx, repo):
    worktree, base = WorktreeManager(repo, ctx.settings.worktree_root).create("f1")
    (worktree / "scratch.txt").write_text("uncommitted")
    task = Task(id="f1", prompt="p", state=TaskState.FAILED, worktree=str(worktree), base_sha=base)
    ctx.store.save(task)
    return task


def _another_failed_task(ctx, repo):
    worktree, base = WorktreeManager(repo, ctx.settings.worktree_root).create("f2")
    ctx.store.save(Task(id="f2", prompt="p", state=TaskState.FAILED, worktree=str(worktree), base_sha=base))


def test_clean_preview_lists_what_would_be_removed_with_a_token(client, failed):
    body = client.get("/clean-preview").json()

    assert [(c["id"], c["state"], c["dirty"], c["directory_exists"]) for c in body["candidates"]] == [("f1", "failed", True, True)]
    assert body["orphans"] == [] and body["skipped"] == []
    assert body["token"]


def test_clean_preview_token_is_stable_until_something_changes(client, ctx, repo, failed):
    first = client.get("/clean-preview").json()["token"]
    assert client.get("/clean-preview").json()["token"] == first
    _another_failed_task(ctx, repo)

    assert client.get("/clean-preview").json()["token"] != first


def test_clean_with_the_shown_token_removes_the_worktree(client, ctx, failed):
    token = client.get("/clean-preview").json()["token"]

    response = client.post("/clean", json={"token": token})

    assert response.status_code == 202
    job = _wait(client, response.json()["job"]["id"])
    assert job["state"] == "succeeded"
    assert not Path(failed.worktree).exists()
    assert ctx.store.load("f1").worktree is None


def test_clean_with_a_stale_token_is_409_and_removes_nothing(client, ctx, repo, failed):
    token = client.get("/clean-preview").json()["token"]
    _another_failed_task(ctx, repo)

    response = client.post("/clean", json={"token": token})

    assert response.status_code == 409
    assert response.json()["error"] == "clean refused"
    assert Path(failed.worktree).exists()


def test_clean_without_a_token_is_422(client):
    assert client.post("/clean", json={}).status_code == 422


CLEAN_CHECK_SENTENCE = "Git could not check the repository, so nothing can be cleaned."

CHECK_FAILURES = [
    subprocess.CalledProcessError(128, ["git", "branch", "--list", "secret-ref"], stderr="fatal: secret"),
    FileNotFoundError(2, "No such file", "/secret/path"),
]


def _failing_clean_candidates(monkeypatch, error):
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr("factory.server.clean_candidates", fail)


def _assert_clean_check_refusal(response):
    assert response.status_code == 409
    assert response.json() == {"error": "clean refused", "detail": CLEAN_CHECK_SENTENCE}
    for leaked in ("secret-ref", "/secret/path", "fatal", "CalledProcessError"):
        assert leaked not in response.text


@pytest.mark.parametrize("error", CHECK_FAILURES, ids=["git failure", "os error"])
def test_a_failed_check_in_clean_preview_is_409_with_a_fixed_sentence(client, monkeypatch, error):
    _failing_clean_candidates(monkeypatch, error)

    _assert_clean_check_refusal(client.get("/clean-preview"))


@pytest.mark.parametrize("error", CHECK_FAILURES, ids=["git failure", "os error"])
def test_a_failed_check_in_clean_is_409_with_a_fixed_sentence_and_queues_nothing(client, monkeypatch, error):
    _failing_clean_candidates(monkeypatch, error)

    _assert_clean_check_refusal(client.post("/clean", json={"token": "0" * 64}))
    assert client.app.state.jobs.list() == []
