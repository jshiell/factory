from datetime import datetime

import pytest
from starlette.testclient import TestClient

from factory.cli import Context
from factory.server import create_secured_app, load_or_create_token
from factory.settings import Settings
from factory.sqlite_db import transaction
from factory.sqlite_ledger import SqliteLedger
from factory.sqlite_store import SqliteTaskStore
from factory.task import Task, TaskState
from factory.web.routes import render_tasks


@pytest.fixture
def ctx(tmp_path):
    settings = Settings(repo=tmp_path, state_dir=tmp_path / "state", worktree_root=tmp_path / "wt")
    settings.state_dir.mkdir()
    return Context(settings, SqliteTaskStore(settings.db_path), SqliteLedger(settings.db_path), stages={},
                   clock=lambda: datetime(2026, 1, 2, 3, 4, 5))


@pytest.fixture
def client(ctx):
    token = load_or_create_token(ctx.settings.state_dir)
    with TestClient(create_secured_app(ctx, token), base_url="http://127.0.0.1:8765") as client:
        client.get("/ui/login", params={"code": client.app.state.login_codes.issue()})
        yield client


def add(ctx, task_id, state=TaskState.QUEUED, goal="a goal", failure_reason=None):
    ctx.store.save(Task(task_id, "prompt", state=state, goal=goal, failure_reason=failure_reason))


def test_the_page_lists_id_state_goal_and_failure_reason(client, ctx):
    add(ctx, "20260102-030405", TaskState.FAILED, goal="fix the widget", failure_reason="gate failed")

    page = client.get("/ui/").text

    for shown in ("20260102-030405", "failed", "fix the widget", "gate failed"):
        assert shown in page


def test_a_long_goal_is_truncated_with_an_ellipsis(client, ctx):
    add(ctx, "20260102-030405", goal="x" * 200)

    page = client.get("/ui/").text

    assert "x" * 120 + "…" in page
    assert "x" * 121 not in page


def test_tasks_are_listed_newest_first(client, ctx):
    add(ctx, "20260101-000000")
    add(ctx, "20260103-000000")
    add(ctx, "20260102-000000")

    page = client.get("/ui/").text

    assert page.index("20260103-000000") < page.index("20260102-000000") < page.index("20260101-000000")


def test_human_gate_states_are_marked_and_others_are_not(client, ctx):
    add(ctx, "20260101-000000", TaskState.QUEUED)
    add(ctx, "20260102-000000", TaskState.REVIEWED)
    add(ctx, "20260103-000000", TaskState.OPEN_QUESTIONS)
    add(ctx, "20260104-000000", TaskState.AUDITED)

    page = client.get("/ui/").text

    assert page.count("needs you") == 3
    assert page.count('class="needs-you"') == 3


def test_skipped_ids_are_listed(client, ctx):
    with transaction(ctx.settings.db_path) as conn:
        conn.execute("INSERT INTO tasks (id, state, data) VALUES ('broken-one', 'queued', 'not json')")

    assert "broken-one" in client.get("/ui/").text


def test_the_page_links_the_static_assets_and_carries_the_htmx_config(client):
    page = client.get("/ui/").text

    assert '<link rel="stylesheet" href="/ui/static/ui.css">' in page
    assert '<script src="/ui/static/htmx.min.js">' in page
    assert '<script src="/ui/static/sse.min.js">' in page
    assert ('<meta name="htmx-config" content=\'{"includeIndicatorStyles": false, "allowEval": false, '
            '"allowScriptTags": false, "historyCacheSize": 0}\'>') in page
    assert "style=" not in page


SCRIPT = "<script>alert(1)</script>"


def test_guard_markup_in_the_goal_and_failure_reason_is_escaped_in_page_and_fragment(client, ctx):
    add(ctx, "20260102-030405", TaskState.FAILED, goal=SCRIPT, failure_reason=SCRIPT)

    for html in (client.get("/ui/").text, render_tasks(ctx.store)):
        assert SCRIPT not in html
        assert html.count("&lt;script&gt;alert(1)&lt;/script&gt;") == 2
