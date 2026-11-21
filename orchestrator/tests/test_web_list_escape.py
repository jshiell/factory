"""The list page shows agent-written text with control and bidi format characters escaped."""
import json

from factory.sqlite_db import transaction
from factory.store import task_to_dict
from factory.task import Task
from factory.web.routes import render_tasks
from webhelpers import add

pytest_plugins = ["webfixtures"]


def test_a_goal_with_control_characters_is_shown_escaped_in_page_and_fragment(client, ctx):
    add(ctx, goal="ring\x07bell‮evil")

    for html in (client.get("/ui/").text, render_tasks(ctx.store)):
        assert "\x07" not in html
        assert "‮" not in html
        assert "ring\\x07bell\\u202eevil" in html


def test_a_failure_reason_with_control_characters_is_shown_escaped_and_a_missing_one_is_blank(client, ctx):
    add(ctx, "20260102-030405", goal="g", failure_reason="gate\x07failed‮")
    add(ctx, "20260102-030406", goal="g")

    for html in (client.get("/ui/").text, render_tasks(ctx.store)):
        assert "\x07" not in html
        assert "‮" not in html
        assert "gate\\x07failed\\u202e" in html
        assert "None" not in html


def test_guard_a_goal_is_truncated_before_it_is_escaped(client, ctx):
    add(ctx, goal="\x07" * 121)

    page = client.get("/ui/").text

    assert "\\x07" * 120 + "…" in page
    assert "\\x07" * 121 not in page


def test_a_skipped_name_with_control_characters_is_shown_escaped(client, ctx):
    with transaction(ctx.settings.db_path) as conn:
        conn.execute("INSERT INTO tasks (id, state, data) VALUES ('broken\x07one', 'queued', 'not json')")

    for html in (client.get("/ui/").text, render_tasks(ctx.store)):
        assert "\x07" not in html
        assert "broken\\x07one" in html


def test_a_task_id_with_control_characters_is_shown_escaped(client, ctx):
    task = Task("a\x07b", "prompt", goal="g")
    with transaction(ctx.settings.db_path) as conn:
        conn.execute("INSERT INTO tasks (id, state, data) VALUES ('safe-column-id', 'queued', ?)",
                     (json.dumps(task_to_dict(task)),))

    for html in (client.get("/ui/").text, render_tasks(ctx.store)):
        assert "\x07" not in html
        assert "a\\x07b" in html
