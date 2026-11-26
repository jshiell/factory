import re

from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"


def test_the_abandon_page_names_the_goal_escaped_says_it_cannot_be_undone_and_posts_to_the_same_path(client, ctx):
    add(ctx, TASK_ID, TaskState.QUEUED, goal="fix <b>the</b> widget\x1b[31m")

    response = client.get(f"/ui/repos/proj/tasks/{TASK_ID}/abandon")

    assert response.status_code == 200
    assert_ui_security_headers(response)
    assert '<link rel="stylesheet" href="/ui/static/ui.css">' in response.text
    assert "fix &lt;b&gt;the&lt;/b&gt; widget" in response.text
    assert "<b>" not in response.text and "\x1b" not in response.text
    assert "cannot be undone" in response.text
    forms = re.findall(r"<form\b[^>]*>.*?</form>", response.text, re.S)
    assert len(forms) == 1
    form = forms[0]
    assert re.search(r'<form[^>]*method="post"', form)
    assert f'action="/ui/repos/proj/tasks/{TASK_ID}/abandon"' in form
    assert len(re.findall(r"<(?:input|button|select|textarea)\b", form)) == 1
    assert re.search(r'<button[^>]*type="submit"', form)
    assert f'<a href="/ui/repos/proj/tasks/{TASK_ID}">' in response.text
    assert "<style" not in response.text and "style=" not in response.text
    assert not re.search(r"<script(?![^>]*\bsrc=)", response.text)
