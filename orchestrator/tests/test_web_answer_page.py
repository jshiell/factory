import re

from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"


def _tags(form, name):
    return re.findall(rf"<{name}\b[^>]*>", form)


def test_the_answer_page_renders_the_question_body_as_markdown(client, ctx):
    questions = ({"number": 1, "title": "t", "body": "pick **one**", "recommended": "x"},)
    add(ctx, TASK_ID, TaskState.OPEN_QUESTIONS, grill_rounds=1, open_questions=questions)

    page = client.get(f"/ui/tasks/{TASK_ID}/answer").text

    assert '<div class="markdown"><p>pick <strong>one</strong></p>' in page


def test_the_answer_page_shows_the_questions_and_a_prefilled_form_that_posts_to_the_same_path(client, ctx):
    questions = ({"number": 1, "title": "which <i>database</i>", "body": "pick one\x1b[31m & stick", "recommended": "sqlite"},
                 {"number": 2, "title": "which port", "body": "any", "recommended": 'a "quoted"\x07 value\nsecond line'})
    add(ctx, TASK_ID, TaskState.OPEN_QUESTIONS, goal="fix <b>the</b> widget", grill_rounds=2, open_questions=questions)

    response = client.get(f"/ui/tasks/{TASK_ID}/answer")

    assert response.status_code == 200
    assert_ui_security_headers(response)
    assert '<link rel="stylesheet" href="/ui/static/ui.css">' in response.text
    for shown in ("fix &lt;b&gt;the&lt;/b&gt; widget", "1. which &lt;i&gt;database&lt;/i&gt;", "pick one\\x1b[31m &amp; stick", "2. which port", "any"):
        assert shown in response.text
    assert "<b>" not in response.text and "<i>" not in response.text and "\x1b" not in response.text and "\x07" not in response.text
    forms = re.findall(r"<form\b[^>]*>.*?</form>", response.text, re.S)
    assert len(forms) == 1
    form = forms[0]
    assert re.search(r'<form[^>]*method="post"', form)
    assert f'action="/ui/tasks/{TASK_ID}/answer"' in form
    hidden, first, second = _tags(form, "input")
    assert 'type="hidden"' in hidden and 'name="round"' in hidden and 'value="2"' in hidden
    assert 'type="text"' in first and 'name="q0"' in first and 'value="sqlite"' in first
    assert 'type="text"' in second and 'name="q1"' in second
    assert 'value="a &#34;quoted&#34;\\x07 value second line"' in second
    assert len(re.findall(r"<(?:input|button|select|textarea)\b", form)) == 4
    assert re.search(r'<button[^>]*type="submit"', form)
    assert f'<a href="/ui/tasks/{TASK_ID}">' in response.text
    assert "<style" not in response.text and "style=" not in response.text
    assert not re.search(r"<script(?![^>]*\bsrc=)", response.text)
