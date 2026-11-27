from factory.evidence import PART_LIMIT, bounded_evidence


def test_a_part_longer_than_the_limit_keeps_only_its_tail_and_says_so():
    text = "head-" + "x" * PART_LIMIT + "-tail"

    result = bounded_evidence([("stderr", text)])

    assert result.startswith("stderr:\n")
    assert result.endswith("-tail") and "head-" not in result
    assert "(earlier output cut)" in result
