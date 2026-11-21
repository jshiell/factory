"""escape_control makes agent-written text safe to show, lone surrogates included."""
from factory.textsafe import escape_control


def test_a_lone_high_surrogate_is_escaped():
    assert escape_control("a\ud800b") == "a\\ud800b"


def test_a_lone_low_surrogate_from_an_undecodable_byte_is_escaped():
    assert escape_control("\udcff") == "\\udcff"


def test_two_surrogate_halves_written_separately_are_not_recombined():
    assert escape_control("\ud83d" + "\ude00") == "\\ud83d\\ude00"
