import pytest

from factory.answer_questions import MAX_ANSWER_BYTES, AnswerRejected, RoundChanged, answer_questions
from factory.store import TaskStore
from factory.task import InvalidTransition, Task, TaskState

QUESTION = {"number": 1, "title": "Scope", "body": "Which module?", "recommended": "calc.py"}


@pytest.fixture
def store(tmp_path):
    store = TaskStore(tmp_path)
    store.save(Task(id="t1", prompt="", state=TaskState.OPEN_QUESTIONS, goal="g", grill=True, grill_rounds=1,
                    open_questions=(QUESTION,)))
    return store


def test_answering_records_the_round_and_returns_the_task_to_proposed(store):
    task = answer_questions("t1", store, 1, "use calc.py")

    assert task.state is TaskState.PROPOSED and task.open_questions == ()
    assert task.grill_log == ({"round": 1, "questions": [QUESTION], "answers": "use calc.py"},)
    assert store.load("t1") == task
