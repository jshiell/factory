from factory.task import TaskState
from factory.textsafe import escape_control
from webhelpers import add

pytest_plugins = ["webfixtures"]

PAGE = "/ui/repos/proj/tasks/20260102-030405"
CONTROL_CHARS = "\x1b\u202e\u200b"


def test_a_question_number_is_shown_once_even_when_numbers_are_not_contiguous(client, ctx):
    questions = ({"number": 3, "title": "which database", "recommended": "sqlite"},
                 {"number": 7, "title": "which port", "recommended": "8765"})
    add(ctx, state=TaskState.OPEN_QUESTIONS, grill_rounds=1, open_questions=questions)

    page = client.get(PAGE).text

    assert "3. which database" in page
    assert "7. which port" in page
    assert "<ol" not in page
    assert "value=" not in page


def test_control_and_bidi_characters_in_a_question_number_are_escaped(client, ctx):
    number = "9" + CONTROL_CHARS
    add(ctx, state=TaskState.OPEN_QUESTIONS, grill_rounds=1,
        open_questions=({"number": number, "title": "t", "recommended": "r"},))

    page = client.get(PAGE).text

    assert not any(char in page for char in CONTROL_CHARS)
    assert f"{escape_control(number)}. t" in page
