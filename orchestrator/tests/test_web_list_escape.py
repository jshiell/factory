"""The list page shows agent-written text with control and bidi format characters escaped."""
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
