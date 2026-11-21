import html
import re

from factory.task import TaskState
from webhelpers import add
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

QUESTIONS = ({"number": 1, "title": "t", "body": "b", "recommended": "plain"},
             {"number": 2, "title": "t", "body": "b", "recommended": "bell\x07 and\nmulti  line\r\nanswer"},
             {"number": 3, "title": "t", "body": "b", "recommended": ' "quoted" & <tagged> ​ '})


def _grill_log_after_posting(client, ctx, task_id, fields_of):
    add(ctx, task_id, TaskState.OPEN_QUESTIONS, grill_rounds=1, open_questions=QUESTIONS)
    fields = fields_of(client.get(f"/ui/tasks/{task_id}/answer").text)
    response = client.post(f"/ui/tasks/{task_id}/answer", data=fields, headers=same_origin_headers(), follow_redirects=False)
    assert response.status_code == 303
    return ctx.store.load(task_id).grill_log


def _the_pages_own_values(page):
    """What a browser submits for the untouched form: each text input's value, with CR and LF stripped."""
    inputs = re.findall(r'<input[^>]*type="text"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', page)
    return {"round": "1", **{name: re.sub(r"[\r\n]", "", html.unescape(value)) for name, value in inputs}}


def test_posting_the_pages_prefilled_values_stores_the_same_entry_as_posting_blanks(client, ctx):
    unchanged = _grill_log_after_posting(client, ctx, "20260102-030405", _the_pages_own_values)
    blanks = _grill_log_after_posting(client, ctx, "20260102-030406", lambda page: {"round": "1"})

    assert unchanged == blanks
    assert unchanged[0]["answers"] == '1. plain\n2. bell\\x07 and multi line\\x0d answer\n3. "quoted" & <tagged> \\u200b'
