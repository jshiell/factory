from factory.web.routes import _TEMPLATES


def humanise(value):
    return _TEMPLATES.from_string("{{ value | humanise }}").render(value=value)


def test_an_enum_value_is_shown_with_a_capital_and_spaces():
    assert humanise("open_questions") == "Open questions"
