from factory.sqlite_db import transaction
from webhelpers import add

pytest_plugins = ["webfixtures"]

UI_JS = '<script src="/ui/static/ui.js">'


def test_the_task_fragment_is_wrapped_in_an_sse_subscription_to_the_task_event(client, ctx):
    add(ctx, "20260102-030405")

    page = client.get("/ui/tasks/20260102-030405").text

    assert 'hx-ext="sse" sse-connect="/ui/tasks/20260102-030405/events" sse-swap="task"' in page


def test_the_task_page_loads_ui_js_after_the_banner_and_has_no_inline_script_or_style(client, ctx):
    add(ctx, "20260102-030405")

    page = client.get("/ui/tasks/20260102-030405").text

    assert page.index(UI_JS) > page.index('id="disconnected"')
    assert "<script>" not in page and "style=" not in page


def test_the_404_and_unreadable_pages_do_not_open_a_stream(client, ctx):
    with transaction(ctx.settings.db_path) as conn:
        conn.execute("INSERT INTO tasks (id, state, data) VALUES ('broken-one', 'queued', 'not json')")

    for task_id in ("20260102-999999", "broken-one"):
        page = client.get(f"/ui/tasks/{task_id}").text

        assert "sse-connect" not in page
        assert "hx-ext" not in page
