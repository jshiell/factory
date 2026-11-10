from datetime import datetime

import pytest

from factory.cli import Context
from factory.settings import Settings
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore
from serving import served
from test_cli import LabelRunner, reaper


@pytest.fixture
def ctx(tmp_path):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt")
    settings.state_dir.mkdir()
    context = Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={},
                      clock=lambda: datetime(2026, 1, 2, 3, 4, 5))
    context.reap = reaper()
    built = []

    def build(settings, eval_dir):
        built.append((settings, eval_dir))
        return LabelRunner()
    context.eval_runner = build
    context.built = built
    return context


@pytest.fixture
def client(ctx):
    with served(ctx) as client:
        yield client


def _wait(client, job_id):
    client.app.state.runner.wait_idle()
    return client.get(f"/jobs/{job_id}").json()


def test_eval_audit_runs_a_job_in_its_own_directory_and_reaps_only_that_directory(client, ctx):
    response = client.post("/eval-audit", json={"runs": 1, "only": ["case-01"]})

    assert response.status_code == 202
    body = response.json()
    eval_dir = ctx.settings.state_dir / "evals" / "20260102T030405"
    assert body["eval_dir"] == str(eval_dir)
    job = _wait(client, body["job"]["id"])
    assert job["state"] == "succeeded"
    assert job["result"]["eval_dir"] == str(eval_dir) and job["result"]["complete"] is True
    assert ctx.built == [(ctx.settings, eval_dir)] and ctx.reap.calls == [eval_dir]
    assert len(list(eval_dir.glob("case-01-r1.json"))) == 1


@pytest.mark.parametrize("body", [{"runs": 0}, {"runs": "x"}, {"max_usd": 0}, {"only": ["nope"]}, {"only": "case-01"}])
def test_eval_audit_with_bad_input_is_422_and_starts_nothing(client, ctx, body):
    response = client.post("/eval-audit", json=body)

    assert response.status_code == 422
    assert not (ctx.settings.state_dir / "evals").exists()


def _first_eval(client):
    queued = client.post("/eval-audit", json={"runs": 1, "only": ["case-01"]}).json()
    _wait(client, queued["job"]["id"])
    return queued["eval_dir"]


def test_resuming_reuses_the_earlier_directory_and_reruns_nothing_valid(client, ctx):
    eval_dir = _first_eval(client)
    ctx.built.clear()
    ctx.reap.calls.clear()

    response = client.post("/eval-audit", json={"runs": 1, "only": ["case-01"], "resume": "20260102T030405"})

    assert response.status_code == 202 and response.json()["eval_dir"] == eval_dir
    job = _wait(client, response.json()["job"]["id"])
    assert job["state"] == "succeeded" and job["result"]["complete"] is True
    assert ctx.reap.calls == [ctx.settings.state_dir / "evals" / "20260102T030405"]
    assert len(list((ctx.settings.state_dir / "evals").iterdir())) == 1


@pytest.mark.parametrize("resume", ["../evals", "a/b", "..", ".", "", "missing", 7, "no-meta"])
def test_resuming_something_that_is_not_an_earlier_eval_is_422_and_runs_nothing(client, ctx, resume):
    _first_eval(client)
    (ctx.settings.state_dir / "evals" / "no-meta").mkdir()
    ctx.built.clear()
    ctx.reap.calls.clear()

    response = client.post("/eval-audit", json={"runs": 1, "resume": resume})

    assert response.status_code == 422 and "resume" in response.json()["detail"]
    assert ctx.built == [] and ctx.reap.calls == []


def test_resuming_against_a_changed_corpus_fails_the_job_with_a_message_and_runs_nothing(client, ctx):
    _first_eval(client)
    meta = ctx.settings.state_dir / "evals" / "20260102T030405" / "meta.json"
    meta.write_text(meta.read_text().replace('"corpus_hash": "', '"corpus_hash": "x'))
    ctx.built.clear()

    response = client.post("/eval-audit", json={"runs": 1, "only": ["case-01"], "resume": "20260102T030405"})

    job = _wait(client, response.json()["job"]["id"])
    assert job["state"] == "failed" and "corpus" in job["error"].lower()


def test_resuming_through_a_symlink_is_422_and_runs_nothing(client, ctx, tmp_path):
    _first_eval(client)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "meta.json").write_text("{}")
    (ctx.settings.state_dir / "evals" / "link").symlink_to(outside)
    ctx.built.clear()
    ctx.reap.calls.clear()

    response = client.post("/eval-audit", json={"runs": 1, "resume": "link"})

    assert response.status_code == 422 and "resume" in response.json()["detail"]
    assert ctx.built == [] and ctx.reap.calls == []
