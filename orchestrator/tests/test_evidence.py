from factory.evidence import PART_LIMIT, TOTAL_LIMIT, bounded_evidence


def test_a_part_longer_than_the_limit_keeps_only_its_tail_and_says_so():
    text = "head-" + "x" * PART_LIMIT + "-tail"

    result = bounded_evidence([("stderr", text)])

    assert result.startswith("stderr:\n")
    assert result.endswith("-tail") and "head-" not in result
    assert "(earlier output cut)" in result


def test_the_value_of_a_secret_environment_variable_is_redacted_even_across_the_cut(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_sekrit")
    monkeypatch.setenv("GITHUB_TOKEN", "")
    text = "x" * (PART_LIMIT - 5) + "ghp_sekrit and tail"

    result = bounded_evidence([("stderr", text), ("other", "plain")])

    assert "ghp_sekrit" not in result and "[redacted]" in result
    assert result.endswith("plain")


def test_the_whole_is_cut_to_the_total_limit_keeping_the_earlier_parts():
    parts = [(f"part{n}", str(n) * PART_LIMIT) for n in range(5)]

    result = bounded_evidence(parts)

    assert "part0:" in result and "part4:" not in result
    assert result.endswith("(further evidence cut)")
    assert len(result) <= TOTAL_LIMIT + len("\n(further evidence cut)")
