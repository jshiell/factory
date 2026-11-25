from factory.web.routes import _TEMPLATES


def humanise(value):
    return _TEMPLATES.from_string("{{ value | humanise }}").render(value=value)


def test_an_enum_value_is_shown_with_a_capital_and_spaces():
    assert humanise("open_questions") == "Open questions"


def test_guard_only_the_first_letter_is_raised_and_other_capitals_stay():
    assert humanise("CalledProcessError") == "CalledProcessError"
    assert humanise("zdata_CalledProcess") == "Zdata CalledProcess"


def test_guard_a_markup_character_is_still_escaped():
    assert humanise("<script>") == "&lt;script&gt;"
