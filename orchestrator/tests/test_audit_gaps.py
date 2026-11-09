from factory.audit_gaps import gaps_to_carry
from factory.task import Task, TaskState


def rejected(*findings, **fields):
    return Task(id="t", prompt="", state=TaskState.FAILED, failure_reason="audit_rejected", audit_findings=findings, **fields)


def test_gaps_are_one_line_and_at_most_500_characters_each():
    assert gaps_to_carry(rejected("a\n\n  b", "x" * 900)) == ("a b", "x" * 500)


def test_at_most_ten_gaps_and_4000_characters_are_carried_in_order():
    ten = tuple(f"{i}" * 450 for i in range(12))

    carried = gaps_to_carry(rejected(*ten))

    assert carried == ten[:8] and sum(map(len, carried)) <= 4000


def test_empty_and_whitespace_only_gaps_are_dropped():
    assert gaps_to_carry(rejected("", " \n ", "real")) == ("real",)


def test_a_gap_quoting_the_required_tag_is_bounded_like_any_other():
    quoted = "Recommend (required): " + "y" * 600

    assert gaps_to_carry(rejected("first", quoted)) == ("first", quoted[:500])
