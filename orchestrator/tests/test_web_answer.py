from factory.task import TaskState
from webhelpers import add
from webposts import same_origin_headers

pytest_plugins = ["webfixtures"]

TASK_ID = "20260102-030405"
QUESTIONS = ({"number": 1, "title": "which database", "body": "pick one", "recommended": "sqlite"},
             {"number": 2, "title": "which port", "body": "any", "recommended": "8765"})


def _post_answer(client, fields):
    return client.post(f"/ui/tasks/{TASK_ID}/answer", data=fields, headers=same_origin_headers(), follow_redirects=False)


def _open(ctx, questions=QUESTIONS, rounds=2):
    add(ctx, TASK_ID, TaskState.OPEN_QUESTIONS, goal="fix the widget", grill_rounds=rounds, open_questions=questions)


def test_posting_every_answer_records_them_in_order_and_redirects_to_the_task_page(client, ctx):
    _open(ctx)

    response = _post_answer(client, {"round": "2", "q0": "postgres", "q1": "9000"})

    assert response.status_code == 303
    assert response.headers["location"] == f"/ui/tasks/{TASK_ID}"
    task = ctx.store.load(TASK_ID)
    assert task.state is TaskState.PROPOSED
    assert task.open_questions == ()
    assert list(task.grill_log) == [{"round": 2, "questions": list(QUESTIONS), "answers": "1. postgres\n2. 9000"}]
