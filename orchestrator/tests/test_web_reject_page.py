import re

from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers

pytest_plugins = ["webfixtures"]


def test_the_confirm_page_shows_the_goal_and_a_form_that_posts_to_the_same_path(client, ctx):
    add(ctx, "20260102-030405", TaskState.REVIEWED, goal="fix <b>the</b> widget\x1b[31m")

    response = client.get("/ui/tasks/20260102-030405/reject")

    assert response.status_code == 200
    assert_ui_security_headers(response)
    assert '<link rel="stylesheet" href="/ui/static/ui.css">' in response.text
    assert "fix &lt;b&gt;the&lt;/b&gt; widget" in response.text
    assert "<b>" not in response.text and "\x1b" not in response.text
    forms = re.findall(r"<form\b[^>]*>.*?</form>", response.text, re.S)
    assert len(forms) == 1
    assert re.search(r'<form[^>]*method="post"', forms[0])
    assert 'action="/ui/tasks/20260102-030405/reject"' in forms[0]
    assert len(re.findall(r"<(?:input|button|select|textarea)\b", forms[0])) == 1
    assert re.search(r'<button[^>]*type="submit"|<input[^>]*type="submit"', forms[0])
    assert '<a href="/ui/tasks/20260102-030405">' in response.text
    assert "<style" not in response.text and "style=" not in response.text
    assert not re.search(r"<script(?![^>]*\bsrc=)", response.text)
