from datetime import datetime

import pytest

from factory.cli import Context
from factory.settings import Settings
from factory.sqlite_db import transaction
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore
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
    _assert_unreadable_and_logged_once(client.get(f"/tasks/{broken_id}"), capsys)

