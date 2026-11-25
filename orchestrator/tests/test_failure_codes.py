from factory.failure_codes import explain


def test_known_failure_reason_has_sentence_and_next_step():
    explanation = explain("failure_reason", "tests_failed")

    assert explanation.sentence
    assert explanation.next_step


def test_unknown_code_has_no_explanation():
    assert explain("failure_reason", "no_such_code") is None


def test_unknown_field_has_no_explanation():
    assert explain("no_such_field", "tests_failed") is None
