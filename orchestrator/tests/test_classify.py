import json
from pathlib import Path

from factory.classify import OutcomeKind, classify

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_stdout(name: str) -> str:
    return (FIXTURES / f"{name}.json").read_text()


def test_empty_stdout_is_unparseable_and_keeps_exit_code():
    outcome = classify("", exit_code=1)

    assert outcome.kind is OutcomeKind.UNPARSEABLE
    assert outcome.exit_code == 1


def test_non_json_stdout_is_unparseable():
    assert classify("not json", exit_code=0).kind is OutcomeKind.UNPARSEABLE


def test_json_that_is_not_an_object_is_unparseable():
    assert classify("[1, 2]", exit_code=0).kind is OutcomeKind.UNPARSEABLE


def test_json_object_without_result_type_is_unparseable():
    assert classify(json.dumps({"type": "other"}), exit_code=0).kind is OutcomeKind.UNPARSEABLE
