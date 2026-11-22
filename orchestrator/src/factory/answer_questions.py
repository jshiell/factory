from dataclasses import replace

from factory.store import TaskStore
from factory.task import Event, Task, apply

MAX_ANSWER_BYTES = 20_000


class RoundChanged(Exception):
    pass


class AnswerRejected(ValueError):
    pass


def answer_questions(task_id: str, store: TaskStore, round_number: int, text: str) -> Task:
    """Record the answers to the open round and send the task back to planning."""
    if not text.strip() or len(text.encode()) > MAX_ANSWER_BYTES:
        raise AnswerRejected(f"answers must be non-empty and at most {MAX_ANSWER_BYTES} bytes")
    def record(task: Task) -> Task:
        answered = apply(task, Event.ANSWERED)
        if round_number != task.grill_rounds:
            raise RoundChanged(task_id)
        entry = {"round": round_number, "questions": list(task.open_questions), "answers": text}
        return replace(answered, open_questions=(), grill_log=(*task.grill_log, entry))

    return store.update(task_id, record)
