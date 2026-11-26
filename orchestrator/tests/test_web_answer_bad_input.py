import asyncio

import pytest
from starlette.requests import Request

from factory.task import TaskState
from factory.web import routes
from webhelpers import add, assert_ui_security_headers
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
QUESTIONS = ({"number": 1, "title": "which database", "body": "pick one", "recommended": "sqlite"},
             {"number": 2, "title": "which port", "body": "any", "recommended": "8765"})
FORM = "application/x-www-form-urlencoded"
MARKER = "ECHOME"
CAP = 64 * 1024


def _form(body, content_type=FORM):
    return body, {"Content-Type": content_type}


BAD_POSTS = {
    "missing round": _form(f"q0={MARKER}"),
    "empty body": _form(""),
    "empty round": _form(f"round=&q0={MARKER}"),
    "round without =": _form(f"round&q0={MARKER}"),
    "non-digit round": _form(f"round=two&q0={MARKER}"),
    "negative round": _form(f"round=-2&q0={MARKER}"),
    "plus round": _form(f"round=%2B2&q0={MARKER}"),
    "fullwidth digit round": _form(f"round=%EF%BC%92&q0={MARKER}"),
    "superscript digit round": _form(f"round=%C2%B2&q0={MARKER}"),
    "leading space round": _form(f"round=+2&q0={MARKER}"),
    "trailing space round": _form(f"round=2%20&q0={MARKER}"),
    "underscore round": _form(f"round=0_2&q0={MARKER}"),
    "round past int's digit limit": _form(f"round={'9' * 5000}&q0={MARKER}"),
    "text/plain": _form(f"round=2&q0={MARKER}", "text/plain"),
    "multipart": _form(f"round=2&q0={MARKER}", "multipart/form-data; boundary=x"),
    "json": _form(f'{{"round": "2", "q0": "{MARKER}"}}', "application/json"),
    "form type with a suffix": _form(f"round=2&q0={MARKER}", FORM + "x"),
    "missing content type": (f"round=2&q0={MARKER}", {}),
    "body over the cap": _form(f"round=2&q0={MARKER}&padding=" + "a" * CAP),  # an ignored field: only the cap refuses it
    "repeated round": _form(f"round=2&round=2&q0={MARKER}"),
    "repeated round, one percent-encoded": _form(f"round=2&%72ound=2&q0={MARKER}"),
    "repeated q field": _form(f"round=2&q0={MARKER}&q0=other"),
    "too many fields": _form(f"round=2&q0={MARKER}" + "&x=1" * 31),
    "answers over 20,000 bytes": _form(f"round=2&q1={MARKER}&q0=" + "a" * 20_000),
    "multibyte answers over 20,000 bytes, under the cap": _form(f"round=2&q1={MARKER}&q0=" + "%C3%A9" * 10_000),
    "non-ASCII bytes": _form(f"round=2&q1={MARKER}&q0=".encode() + "é".encode()),
}


def _open(ctx):
    add(ctx, TASK_ID, TaskState.OPEN_QUESTIONS, goal="fix the widget", grill_rounds=2, open_questions=QUESTIONS)
    return ctx.store.load(TASK_ID)


def _post(client, body, headers):
    return client.post(f"/ui/repos/proj/tasks/{TASK_ID}/answer", content=body, headers={**same_origin_headers(), **headers},
                       follow_redirects=False)


@pytest.mark.parametrize("body, headers", BAD_POSTS.values(), ids=BAD_POSTS.keys())
def test_a_bad_answer_form_is_a_422_page_that_changes_nothing_and_echoes_nothing(client, ctx, body, headers):
    before = _open(ctx)

    response = _post(client, body, headers)

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert_ui_security_headers(response)
    assert '<link rel="stylesheet" href="/ui/static/ui.css">' in response.text
    assert '<a href="/ui/">' in response.text
    assert "<form" not in response.text
    for echoed in (TASK_ID, MARKER, "fix the widget", "which database", "Traceback"):
        assert echoed not in response.text
    assert ctx.store.load(TASK_ID) == before


@pytest.mark.parametrize("content_type", [FORM + "; charset=UTF-8", "Application/X-WWW-Form-URLEncoded"])
def test_the_form_type_is_matched_without_its_parameters_or_case(client, ctx, content_type):
    _open(ctx)

    response = _post(client, "round=2&q0=postgres", {"Content-Type": content_type})

    assert response.status_code == 303
    assert ctx.store.load(TASK_ID).state is TaskState.PROPOSED


def test_a_form_of_exactly_the_cap_is_read(client, ctx):
    _open(ctx)
    head = "round=2&q0=postgres&padding="
    body = head + "a" * (CAP - len(head))

    response = _post(client, body, {"Content-Type": FORM})

    assert len(body) == CAP
    assert response.status_code == 303


def test_the_form_reader_stops_at_the_cap_without_reading_the_rest_of_an_endless_body():
    chunk, received = b"a" * 1024, []

    async def endless_body():
        received.append(chunk)
        assert len(received) <= 2 * CAP // len(chunk), "read on past the cap"  # fail, not hang, if the cap is ignored
        return {"type": "http.request", "body": chunk, "more_body": True}

    request = Request({"type": "http", "method": "POST", "headers": [(b"content-type", FORM.encode())]}, endless_body)

    async def read():
        with pytest.raises(routes._BadForm):
            await routes._read_form(request)

    asyncio.run(read())
    assert len(received) == CAP // len(chunk) + 1
