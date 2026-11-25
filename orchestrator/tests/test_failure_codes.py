from factory.failure_codes import explain


def test_known_failure_reason_has_sentence_and_next_step():
    explanation = explain("failure_reason", "tests_failed")

    assert explanation.sentence
    assert explanation.next_step


def test_unknown_code_has_no_explanation():
    assert explain("failure_reason", "no_such_code") is None


def test_text_joins_sentence_and_next_step():
    assert explain("failure_reason", "timeout").text == "The agent ran out of time. Retry the task."


def test_text_is_just_the_sentence_without_a_next_step():
    assert explain("failure_reason", "abandoned").text == "The task was abandoned."


def test_advice_depends_on_the_field():
    as_failure = explain("failure_reason", "timeout")
    as_stage_error = explain("plan_error", "timeout")

    assert as_failure != as_stage_error
    assert "advance" in as_stage_error.next_step.lower()


def test_fix_prefix_composes_the_inner_sentence():
    inner = explain("failure_reason", "tests_failed")
    composed = explain("failure_reason", "fix_tests_failed")

    assert composed.sentence == f"While fixing the failing tests: {inner.sentence}"
    assert composed.next_step == inner.next_step


def test_fix_prefix_on_unknown_inner_code_has_no_explanation():
    assert explain("failure_reason", "fix_no_such_code") is None


def test_unknown_field_has_no_explanation():
    assert explain("no_such_field", "tests_failed") is None
