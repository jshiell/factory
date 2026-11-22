from factory.answer_questions import answer_questions
from factory.store import TaskStore
from factory.task import Task, TaskState
from updateonly import UpdateOnly

QUESTION = {"number": 1, "title": "Scope", "body": "Which module?", "recommended": "calc.py"}


def test_answering_questions_writes_through_update_not_save(tmp_path):
    inner = TaskStore(tmp_path)
    inner.save(Task(id="t1", prompt="", state=TaskState.OPEN_QUESTIONS, goal="g", grill=True, grill_rounds=1,
                    open_questions=(QUESTION,)))
    store = UpdateOnly(inner)

    saved = answer_questions("t1", store, 1, "calc.py")

    assert saved.open_questions == ()
    assert store.updates == ["t1"]
    assert inner.load("t1") == saved
