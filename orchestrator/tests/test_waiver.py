import pytest

from factory.review_notes import MAX_ITEM, MAX_ITEMS, TRUNCATED, capped_findings
from factory.waiver import Approve, Refuse, Waive, parse_answer, remove_waived

REQUIRED = "Recommend (required): "


@pytest.mark.parametrize("notes", [
    tuple(f"{REQUIRED}note {i}" for i in range(3)),
    tuple(f"{REQUIRED}note {i}" for i in range(MAX_ITEMS + 1)),
    tuple(f"{REQUIRED}{i}" + "x" * (MAX_ITEM - 40) for i in range(MAX_ITEMS + 1)),
], ids=["fits", "ten-and-the-marker", "over-the-total-limit"])
def test_capping_the_notes_left_after_a_waiver_changes_nothing(notes):
    # guard (green on arrival; mutation-checked): the stored reduced list is shown and sent as it is
    shown = capped_findings(notes)
    reduced = remove_waived(shown, (shown[0],))

    assert capped_findings(reduced) == reduced
    assert (TRUNCATED in shown) == (TRUNCATED in reduced)


@pytest.mark.parametrize("text", ["y", "  y \n", "y\n"])
def test_y_approves_without_waiving(text):
    assert parse_answer(text, required_count=2) == Approve()


@pytest.mark.parametrize("text", ["", "n", "yes", "Y y", "Y", "ok"])
def test_anything_else_is_not_approved_and_needs_no_explanation(text):
    assert parse_answer(text, required_count=2) == Refuse(reason=None)


@pytest.mark.parametrize("text, numbers", [("w 1", (1,)), ("w 1 3", (1, 3)), ("  w 3   1 \n", (3, 1))])
def test_w_with_note_numbers_waives_those_notes(text, numbers):
    assert parse_answer(text, required_count=3) == Waive(numbers=numbers)


@pytest.mark.parametrize("text", ['w', ' w \\n'])
def test_w_without_note_numbers_is_refused(text):
    answer = parse_answer(text, required_count=3)

    assert isinstance(answer, Refuse) and answer.reason


@pytest.mark.parametrize("text", ['w x', 'w ²', 'w -1', 'w 1.5', 'w +1', 'w ١'])
def test_a_note_number_must_be_plain_ascii_digits(text):
    answer = parse_answer(text, required_count=3)

    assert isinstance(answer, Refuse) and answer.reason


@pytest.mark.parametrize("text", ['w 0', 'w 4', 'w 1 9', 'w 00'])
def test_a_note_number_must_be_one_of_the_shown_required_notes(text):
    answer = parse_answer(text, required_count=3)

    assert isinstance(answer, Refuse) and answer.reason
