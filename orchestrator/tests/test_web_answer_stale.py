from factory.task import TaskState
from webhelpers import add, assert_ui_security_headers
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
QUESTIONS = ({"number": 1, "title": "which database", "body": "pick one", "recommended": "sqlite"},)


def test_posting_an_answer_for_an_old_round_is_a_409_questions_changed_page_and_changes_nothing(client, ctx):
    open_task = add(ctx, TASK_ID, TaskState.OPEN_QUESTIONS, goal="fix the widget", grill_rounds=3, open_questions=QUESTIONS)

    response = client.post(f"/ui/tasks/{TASK_ID}/answer", data={"round": "2", "q0": "postgres"},
                           headers=same_origin_headers(), follow_redirects=False)

    assert response.status_code == 409
    assert response.headers["content-type"].startswith("text/html")
    assert_ui_security_headers(response)
    assert "questions changed" in response.text
    assert "<form" not in response.text
    for echoed in (TASK_ID, "fix the widget", "postgres"):
        assert echoed not in response.text
    assert ctx.store.load(TASK_ID) == open_task
