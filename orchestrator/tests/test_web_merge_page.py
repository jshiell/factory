import re
from dataclasses import replace

import pytest

from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers

pytest_plugins = ["webfixtures", "webmerge"]

TASK_ID = "20260102-030405"
HOSTILE = "<script>alert(1)</script>\x1b[31m & \"quoted\""
ESCAPED = "&lt;script&gt;alert(1)&lt;/script&gt;\\x1b[31m &amp; &#34;quoted&#34;"
NOT_MERGEABLE = [state for state in TaskState if state is not TaskState.AUDITED]


def _forms(text):
    return re.findall(r"<form\b[^>]*>.*?</form>", text, re.S)


def test_the_merge_page_shows_what_will_be_merged_and_posts_the_audited_sha_to_the_same_path(client, audited):
    response = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/merge")

    assert response.status_code == 200
    assert_ui_security_headers(response)
    text = response.text
    assert "fix the widget" in text
    assert f"factory/{TASK_ID}" in text and "b.txt" in text and "looks fine" in text
    assert "fast-forward" in text and "cannot be undone" in text
    forms = _forms(text)
    assert len(forms) == 1
    assert f'action="/ui/repos/proj/tasks/{TASK_ID}/merge"' in forms[0]
    assert f'<input type="hidden" name="audited_sha" value="{audited.audited_sha}">' in forms[0]
    assert len(re.findall(r"<(?:input|button|select|textarea)\b", forms[0])) == 2


def _assert_refusal_page(response, message):
    assert response.status_code == 409
    assert response.headers["content-type"].startswith("text/html")
    assert_ui_security_headers(response)
    assert message in response.text
    assert '<a href="/ui/">' in response.text
    assert "<form" not in response.text
    for echoed in ("fix the widget", "Traceback"):
        assert echoed not in response.text


@pytest.mark.parametrize("state", NOT_MERGEABLE)
def test_the_merge_page_of_a_task_that_is_not_audited_is_a_409_without_form_or_id(client, ctx, state):
    task = add(ctx, TASK_ID, state, goal="fix the widget")

    response = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/merge")

    _assert_refusal_page(response, "cannot be merged")
    assert TASK_ID not in response.text
    assert ctx.store.load(TASK_ID) == task


def test_the_merge_page_of_an_audited_task_with_a_fix_agent_in_flight_is_a_409(client, ctx, audited):
    ctx.store.save(replace(audited, fix_in_flight=True))

    response = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/merge")

    _assert_refusal_page(response, "cannot be merged in its current state")
    assert audited.audited_sha not in response.text


@pytest.mark.parametrize("started", [False, True], ids=["queued", "running"])
def test_the_merge_page_of_an_audited_task_with_an_unfinished_job_is_a_409_busy_page(client, audited, started):
    job = client.app.state.repos["proj"].jobs.create("advance", TASK_ID)
    if started:
        client.app.state.repos["proj"].jobs.mark_running(job.id)

    response = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/merge")

    _assert_refusal_page(response, "already has a job queued or running")
    assert audited.audited_sha not in response.text


def test_everything_on_the_merge_page_is_escaped(client, audit):
    audit(filename="<i>x.txt", goal=HOSTILE, audit_findings=(HOSTILE, "<b>bold</b>"))

    text = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/merge").text

    assert text.count(ESCAPED) == 2
    assert "&lt;i&gt;x.txt" in text and "&lt;b&gt;bold&lt;/b&gt;" in text
    for raw in ("<script>", "\x1b", "<i>", "<b>"):
        assert raw not in text


def test_the_merge_page_flags_root_tool_config_escaped(client, audit):
    audit(filename=".claude/<i>x\x1b.json")

    text = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/merge").text

    assert "Root tool config" in text
    assert "<li>.claude/&lt;i&gt;x\\x1b.json</li>" in text and "\x1b" not in text


def test_the_merge_page_shows_no_tool_config_note_for_an_ordinary_change(client, audited):
    assert "Root tool config" not in client.get(f"/ui/repos/proj/tasks/{TASK_ID}/merge").text


def test_a_task_without_a_goal_shows_its_escaped_prompt_on_the_merge_page(client, audit):
    audit(goal=None, prompt="do <i>this</i>")

    text = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/merge").text

    assert "do &lt;i&gt;this&lt;/i&gt;" in text and "<i>" not in text
