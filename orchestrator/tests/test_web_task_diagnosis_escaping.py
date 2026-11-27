from factory.task import TaskState
from factory.web.routes import _event, _task_fragment
from test_web_task_job import PAGE, TASK
from webhelpers import add

pytest_plugins = ["webfixtures"]

HOSTILE = {"cause": "<script>alert(1)</script> ‮ reversed", "evidence": ["<img src=x onerror=y>\r\ndata: forged"],
           "next_step": "<b>bold</b>"}


def pages(client, ctx):
    fragment = _task_fragment(ctx.store, client.app.state.repos["proj"].jobs, TASK, "/ui/repos/proj")
    return [client.get(PAGE).text, fragment]


def test_guard_agent_written_markup_is_escaped_on_the_page_and_in_the_fragment(client, ctx):
    # guard (green on arrival): the diagnosis view escapes every field and the template autoescapes
    add(ctx, TASK, TaskState.FAILED, failure_reason="tests_failed", diagnosis=HOSTILE)

    for page in pages(client, ctx):
        assert "<script>alert" not in page and "<img src=x" not in page and "<b>bold" not in page
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_guard_a_bidi_override_in_the_diagnosis_is_shown_escaped_not_applied(client, ctx):
    add(ctx, TASK, TaskState.FAILED, failure_reason="tests_failed", diagnosis=HOSTILE)

    for page in pages(client, ctx):
        assert "‮" not in page and "\\u202e" in page


def test_guard_a_carriage_return_in_the_diagnosis_cannot_forge_an_sse_line(client, ctx):
    add(ctx, TASK, TaskState.FAILED, failure_reason="tests_failed", diagnosis=HOSTILE)
    fragment = _task_fragment(ctx.store, client.app.state.repos["proj"].jobs, TASK, "/ui/repos/proj")

    event = _event("task", fragment)

    assert "\r" not in event
    assert all(line.startswith("data: ") or line in ("event: task", "") for line in event.split("\n"))
    assert "\\x0d" in event
