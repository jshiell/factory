from dataclasses import replace
from datetime import datetime

import pytest

from factory.cli import Context
from factory.settings import Settings
from factory.sqlite_db import transaction
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore
from factory.task import Task, TaskState
from serving import served

UNREADABLE_BODY = {"error": "task unreadable", "detail": "task record is unreadable"}


@pytest.fixture
def ctx(tmp_path):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt")
    settings.state_dir.mkdir()
    return Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={},
                   clock=lambda: datetime(2026, 1, 2, 3, 4, 5))


@pytest.fixture
def client(ctx):
    with served(ctx) as client:
        yield client


@pytest.fixture
def broken_id(ctx):
    with transaction(ctx.settings.db_path) as conn:
        conn.execute("INSERT INTO tasks (id, state, data) VALUES ('broken-one', 'queued', 'not json')")
    return "broken-one"


def _assert_unreadable_and_logged_once(response, capsys):
    assert response.status_code == 500
    assert response.json() == UNREADABLE_BODY
    assert "not json" not in response.text and "JSONDecodeError" not in response.text
    logged = capsys.readouterr().err.splitlines()
    assert len(logged) == 1 and "JSONDecodeError" in logged[0]


def test_showing_an_unreadable_task_answers_a_fixed_500_and_logs_the_error(client, broken_id, capsys):
    _assert_unreadable_and_logged_once(client.get(f"/repos/proj/tasks/{broken_id}"), capsys)


def test_rejecting_an_unreadable_task_answers_a_fixed_500_and_logs_the_error(client, broken_id, capsys):
    _assert_unreadable_and_logged_once(client.post(f"/repos/proj/tasks/{broken_id}/reject"), capsys)


LOADING_ROUTES = [
    ("GET", "/repos/proj/tasks/{id}/approval"), ("POST", "/repos/proj/tasks/{id}/approve"), ("GET", "/repos/proj/tasks/{id}/questions"),
    ("POST", "/repos/proj/tasks/{id}/answer"), ("GET", "/repos/proj/tasks/{id}/merge-preview"), ("POST", "/repos/proj/tasks/{id}/merge"),
    ("GET", "/repos/proj/tasks/{id}/retry-preview"), ("POST", "/repos/proj/tasks/{id}/retry"), ("POST", "/repos/proj/tasks/{id}/advance"),
    ("POST", "/repos/proj/tasks/{id}/abandon"), ("POST", "/repos/proj/tasks/{id}/recover"), ("POST", "/repos/proj/tasks/{id}/rebase"),
    ("GET", "/repos/proj/costs?task_id={id}"),
]


@pytest.mark.parametrize(("method", "path"), LOADING_ROUTES)
def test_every_route_that_loads_a_task_answers_the_fixed_500_for_an_unreadable_one(
        client, broken_id, capsys, method, path):
    _assert_unreadable_and_logged_once(client.request(method, path.format(id=broken_id)), capsys)


class _BrokenLedger:
    def entries(self):
        raise KeyError("cost_usd")


def test_a_failure_after_the_task_loaded_is_not_reported_as_an_unreadable_task(ctx):
    ctx.store.save(Task(id="fine", prompt="", state=TaskState.PROPOSED))
    broken = replace(ctx, ledger=_BrokenLedger())

    with served(broken) as client, pytest.raises(KeyError, match="cost_usd"):
        client.get("/repos/proj/costs?task_id=fine")
