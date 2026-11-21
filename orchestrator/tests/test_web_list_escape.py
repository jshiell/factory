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
