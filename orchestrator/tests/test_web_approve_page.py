import re

from factory.digest import plan_digest
from factory.review_notes import MAX_ITEMS
from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
PLAN = "1. add <script>alert(1)</script>\x1b[31m & test"


def _reviewed(ctx, findings, **fields):
    return add(ctx, TASK_ID, TaskState.REVIEWED, goal="fix <b>the</b> widget", plan=PLAN, plan_digest=plan_digest(PLAN),
               plan_risks=("a <i>risky</i> step",), review_findings=findings, **fields)


def test_the_approve_page_shows_where_each_plan_link_really_goes(client, ctx):
    plan = "see [the docs](https://evil.test/x)"
    add(ctx, TASK_ID, TaskState.REVIEWED, plan=plan, plan_digest=plan_digest(plan))

    page = client.get(f"/ui/tasks/{TASK_ID}/approve").text

    assert "the docs</a> (https://evil.test/x)" in page


def test_the_approve_page_shows_what_the_cli_preview_shows_and_a_form_posting_the_digest_to_the_same_path(client, ctx):
    findings = ("Recommend (required): cover <em>edge</em> cases", "Recommend (optional): rename it", "Recommend (required): add a test")
    _reviewed(ctx, findings)

    response = client.get(f"/ui/tasks/{TASK_ID}/approve")

    assert response.status_code == 200
    assert_ui_security_headers(response)
    assert '<link rel="stylesheet" href="/ui/static/ui.css">' in response.text
    for shown in ("fix &lt;b&gt;the&lt;/b&gt; widget", "a &lt;i&gt;risky&lt;/i&gt; step"):
        assert shown in response.text
    assert "<li>add &lt;script&gt;alert(1)&lt;/script&gt;\\x1b[31m &amp; test</li>" in response.text
    pre = re.findall(r"<pre>(.*?)</pre>", response.text, re.S)
    assert "[1] Recommend (required): cover &lt;em&gt;edge&lt;/em&gt; cases" in pre[0]
    assert "  - Recommend (optional): rename it" in pre[0]
    assert "[2] Recommend (required): add a test" in pre[0]
    assert "WARNING" not in response.text
    assert "kept in the agent prompt and the audit, except any listed as dropped" in response.text
    assert "factory approve" in response.text
    assert "<b>" not in response.text and "<script>" not in response.text and "\x1b" not in response.text
    forms = re.findall(r"<form\b[^>]*>.*?</form>", response.text, re.S)
    assert len(forms) == 1
    form = forms[0]
    assert re.search(r'<form[^>]*method="post"', form)
    assert f'action="/ui/tasks/{TASK_ID}/approve"' in form
    (hidden,) = re.findall(r"<input\b[^>]*>", form)
    assert 'type="hidden"' in hidden and 'name="plan_digest"' in hidden and f'value="{plan_digest(PLAN)}"' in hidden
    assert len(re.findall(r"<(?:input|button|select|textarea)\b", form)) == 2
    assert re.search(r'<button[^>]*type="submit"', form)
    assert f'<a href="/ui/tasks/{TASK_ID}">' in response.text
    assert "<style" not in response.text and "style=" not in response.text
    assert not re.search(r"<script(?![^>]*\bsrc=)", response.text)


def test_the_approve_page_warns_when_the_cap_drops_required_notes(client, ctx):
    _reviewed(ctx, tuple(f"Recommend (required): note {i}" for i in range(MAX_ITEMS + 2)))

    response = client.get(f"/ui/tasks/{TASK_ID}/approve")

    assert f"WARNING: 2 required note(s) beyond the first {MAX_ITEMS} were dropped" in response.text
    assert f"[{MAX_ITEMS}] Recommend (required): note {MAX_ITEMS - 1}" in response.text
    assert f"note {MAX_ITEMS}" not in response.text
