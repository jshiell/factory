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


def test_success_fixture_is_success_with_cost():
    outcome = classify(fixture_stdout("success"), exit_code=0)

    assert outcome.kind is OutcomeKind.SUCCESS
    assert outcome.cost_usd == 0.047295500000000004
    assert outcome.exit_code == 0


def test_budget_fixture_is_budget_exhausted_despite_is_error():
    outcome = classify(fixture_stdout("budget"), exit_code=1)

    assert outcome.kind is OutcomeKind.BUDGET_EXHAUSTED
    assert outcome.cost_usd == 0.0689462
    assert outcome.errors == ("Reached maximum budget ($0.02)",)


def test_is_error_with_other_subtype_is_error_keeping_subtype_and_errors():
    stdout = json.dumps(
        {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "total_cost_usd": 0.01,
            "errors": ["boom"],
        }
    )

    outcome = classify(stdout, exit_code=1)

    assert outcome.kind is OutcomeKind.ERROR
    assert outcome.subtype == "error_during_execution"
    assert outcome.errors == ("boom",)


def test_denials_fixture_is_success_with_denials_carrying_output():
    outcome = classify(fixture_stdout("denials"), exit_code=0)

    assert outcome.kind is OutcomeKind.SUCCESS_WITH_DENIALS
    assert len(outcome.denials) == 5
    assert outcome.structured_output["tests_passed"] is True
