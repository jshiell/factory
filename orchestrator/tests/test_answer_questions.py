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


def test_a_task_that_is_not_waiting_for_answers_refuses_them(store):
    # guard (green on arrival): the ANSWERED transition only exists from open_questions
    store.save(Task(id="t2", prompt="", state=TaskState.PROPOSED, goal="g", grill=True))

    with pytest.raises(InvalidTransition):
        answer_questions("t2", store, 0, "x")

    assert store.load("t2").grill_log == ()


def test_answers_for_a_different_round_are_refused_and_change_nothing(store):
    before = store.load("t1")

    with pytest.raises(RoundChanged):
        answer_questions("t1", store, 2, "stale")

    assert store.load("t1") == before


@pytest.mark.parametrize("text", ["", "  \n", "x" * (MAX_ANSWER_BYTES + 1)])
def test_missing_or_oversize_answers_are_refused_and_change_nothing(store, text):
    before = store.load("t1")

    with pytest.raises(AnswerRejected):
        answer_questions("t1", store, 1, text)

    assert store.load("t1") == before


def test_answering_records_the_round_and_returns_the_task_to_proposed(store):
    task = answer_questions("t1", store, 1, "use calc.py")

    assert task.state is TaskState.PROPOSED and task.open_questions == ()
    assert task.grill_log == ({"round": 1, "questions": [QUESTION], "answers": "use calc.py"},)
    assert store.load("t1") == task
