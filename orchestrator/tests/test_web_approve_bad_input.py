import pytest

from factory.digest import plan_digest
from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
PLAN = "1. add the thing"
DIGEST = plan_digest(PLAN)
FORM = "application/x-www-form-urlencoded"
MARKER = "ECHOME"

BAD_POSTS = {
    "missing digest": (f"other={MARKER}", FORM),
    "empty body": ("", FORM),
    "empty digest": (f"plan_digest=&other={MARKER}", FORM),
    "digest without =": (f"plan_digest&other={MARKER}", FORM),
    "repeated digest": (f"plan_digest={DIGEST}&plan_digest={DIGEST}&other={MARKER}", FORM),
    "repeated digest, one percent-encoded": (f"plan_digest={DIGEST}&%70lan_digest={DIGEST}", FORM),
    "text/plain": (f"plan_digest={DIGEST}&other={MARKER}", "text/plain"),
    "multipart": (f"plan_digest={DIGEST}&other={MARKER}", "multipart/form-data; boundary=x"),
    "json": (f'{{"plan_digest": "{DIGEST}", "other": "{MARKER}"}}', "application/json"),
    "body over the cap": (f"plan_digest={DIGEST}&padding=" + "a" * (64 * 1024), FORM),
    "too many fields": (f"plan_digest={DIGEST}" + "&x=1" * 32, FORM),
    "non-ASCII bytes": (f"plan_digest={DIGEST}&other=é", FORM),
}


@pytest.mark.parametrize("body, content_type", BAD_POSTS.values(), ids=BAD_POSTS.keys())
def test_a_bad_approve_form_is_a_422_page_that_changes_nothing_and_echoes_nothing(client, ctx, body, content_type):
    before = add(ctx, TASK_ID, TaskState.REVIEWED, goal="fix the widget", plan=PLAN, plan_digest=DIGEST)

    response = client.post(f"/ui/repos/proj/tasks/{TASK_ID}/approve", content=body.encode(), follow_redirects=False,
                           headers={**same_origin_headers(), "Content-Type": content_type})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert_ui_security_headers(response)
    assert '<link rel="stylesheet" href="/ui/static/ui.css">' in response.text
    assert '<a href="/ui/">' in response.text
    assert "<form" not in response.text
    for echoed in (TASK_ID, MARKER, DIGEST, "fix the widget", "Traceback"):
        assert echoed not in response.text
    assert ctx.store.load(TASK_ID) == before
