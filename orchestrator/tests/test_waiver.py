import pytest

from factory.review_notes import MAX_ITEM, MAX_ITEMS, TRUNCATED, capped_findings
from factory.waiver import remove_waived

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
