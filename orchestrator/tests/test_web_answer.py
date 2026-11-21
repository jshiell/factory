import pytest

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


@pytest.mark.parametrize("fields, answers", [
    ({"q0": "", "q1": ""}, "1. sqlite\n2. 8765"),
    ({"q0": "   ", "q1": "\t\n"}, "1. sqlite\n2. 8765"),
    ({}, "1. sqlite\n2. 8765"),
    ({"q1": "9000"}, "1. sqlite\n2. 9000"),
    ({"q0": "  postgres  ", "q1": " 9000\t"}, "1. postgres\n2. 9000"),
    ({"q0": "post\ngres", "q1": "90\r\n00"}, "1. post gres\n2. 90 00"),
])
def test_a_blank_or_missing_answer_takes_the_recommended_one_and_a_typed_one_is_trimmed_to_a_line(client, ctx, fields, answers):
    _open(ctx)

    response = _post_answer(client, {"round": "2", **fields})

    assert response.status_code == 303
    assert ctx.store.load(TASK_ID).grill_log[0]["answers"] == answers
