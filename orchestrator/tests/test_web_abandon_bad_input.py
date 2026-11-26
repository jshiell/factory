import pytest

from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
FORM = "application/x-www-form-urlencoded"
MARKER = "ECHOME"

BAD_POSTS = {
    "no content type": (f"other={MARKER}", None),
    "text/plain": (f"other={MARKER}", "text/plain"),
    "multipart": (f"other={MARKER}", "multipart/form-data; boundary=x"),
    "json": (f'{{"other": "{MARKER}"}}', "application/json"),
    "body over the cap": (f"other={MARKER}&padding=" + "a" * (64 * 1024), FORM),
    "too many fields": (f"other={MARKER}" + "&x=1" * 32, FORM),
    "non-ASCII bytes": (f"other={MARKER}é", FORM),
}


def _post(client, body, content_type):
    headers = same_origin_headers() if content_type is None else {**same_origin_headers(), "Content-Type": content_type}
    return client.post(f"/ui/repos/proj/tasks/{TASK_ID}/abandon", content=body.encode(), headers=headers, follow_redirects=False)


@pytest.mark.parametrize("body, content_type", BAD_POSTS.values(), ids=BAD_POSTS.keys())
def test_a_bad_abandon_form_is_a_422_page_that_changes_nothing_and_echoes_nothing(client, ctx, body, content_type):
    before = add(ctx, TASK_ID, TaskState.QUEUED, goal="fix the widget")

    response = _post(client, body, content_type)

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert_ui_security_headers(response)
    assert '<a href="/ui/">' in response.text
    assert "<form" not in response.text
    for echoed in (TASK_ID, MARKER, "fix the widget", "Traceback"):
        assert echoed not in response.text
    assert client.app.state.repos["proj"].jobs.list() == []
    assert ctx.store.load(TASK_ID) == before


def test_an_extra_form_field_is_ignored(client, ctx):
    add(ctx, TASK_ID, TaskState.QUEUED, goal="fix the widget")

    assert _post(client, f"other={MARKER}", FORM).status_code == 303


def test_a_task_that_cannot_be_abandoned_gets_the_409_before_the_body_is_read(client, ctx):
    add(ctx, TASK_ID, TaskState.MERGED, goal="fix the widget")

    assert _post(client, "x=1", "text/plain").status_code == 409
