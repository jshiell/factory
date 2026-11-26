from urllib.parse import urlencode

import pytest

from factory import service
from factory.task import TaskState
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

FORM = "application/x-www-form-urlencoded"


def _post(client, body: str, content_type: str = FORM):
    body = f"repo=proj&{body}" if body and content_type == FORM else body
    return client.post("/ui/tasks/new", content=body.encode(), follow_redirects=False,
                       headers={**same_origin_headers(), "Content-Type": content_type})


def _only_task(ctx):
    (task,) = ctx.store.list().tasks
    return task


def test_posting_a_goal_creates_a_proposed_task_without_starting_it_and_redirects_to_it(client, ctx):
    response = _post(client, urlencode({"goal": "fix the widget", "grill": "true"}))

    task = _only_task(ctx)
    assert response.status_code == 303
    assert response.headers["location"] == f"/ui/repos/proj/tasks/{task.id}"
    assert task.state is TaskState.PROPOSED
    assert (task.goal, task.grill) == ("fix the widget", True)
    client.app.state.runner.wait_idle()
    assert client.app.state.repos["proj"].jobs.latest_for_task(task.id) is None


def test_a_form_without_the_grill_box_ticked_creates_a_task_with_grill_off(client, ctx):
    _post(client, urlencode({"goal": "fix the widget"}))

    assert _only_task(ctx).grill is False


def test_a_blank_goal_shows_the_form_again_with_a_message_and_creates_nothing(client, ctx):
    response = _post(client, urlencode({"goal": "  \r\n\t ", "grill": "true"}))

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("text/html")
    assert "The goal cannot be blank." in response.text
    assert '<form method="post" action="/ui/tasks/new">' in response.text
    assert ctx.store.list().tasks == []


BAD_POSTS = {
    "missing goal": ("grill=true", FORM),
    "empty body": ("", FORM),
    "repeated goal": ("goal=a&goal=b", FORM),
    "repeated goal, one percent-encoded": ("goal=a&%67oal=b", FORM),
    "repeated grill": ("goal=a&grill=true&grill=true", FORM),
    "text/plain": ("goal=a", "text/plain"),
    "body over the cap": ("goal=" + "a" * (64 * 1024), FORM),
}


@pytest.mark.parametrize("body, content_type", BAD_POSTS.values(), ids=BAD_POSTS.keys())
def test_a_bad_new_task_form_is_the_422_page_and_creates_nothing(client, ctx, body, content_type):
    response = _post(client, body, content_type)

    assert response.status_code == 422
    assert "<form" not in response.text
    assert ctx.store.list().tasks == []


def test_a_lone_surrogate_goal_cannot_crash_the_route_or_reach_the_store(client, ctx):
    response = _post(client, "goal=%ED%A0%80")  # the UTF-8 encoding of U+D800, which strict decoding refuses

    assert response.status_code in (303, 422)
    assert all("\ud800" not in (task.goal or "") for task in ctx.store.list().tasks)


def test_a_goal_that_cannot_be_encoded_is_the_form_message_not_a_crash(client, ctx, monkeypatch):
    monkeypatch.setattr("factory.web.routes._read_form", _async_form({"goal": ["\ud800"], "repo": ["proj"]}))

    response = _post(client, "ignored")

    assert response.status_code == 422
    assert "Goal must be valid text." in response.text
    assert ctx.store.list().tasks == []


def _async_form(form):
    async def read(request):
        return form
    return read


def test_an_over_long_goal_shows_the_form_again_with_the_limit_escaped_and_creates_nothing(client, ctx):
    goal = "<b>x</b>" + "a" * service.MAX_GOAL_BYTES

    response = _post(client, urlencode({"goal": goal}))

    assert response.status_code == 422
    assert f"Goal must be at most {service.MAX_GOAL_BYTES} bytes." in response.text
    assert "<b>x</b>" not in response.text
    assert ctx.store.list().tasks == []
