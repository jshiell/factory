import json

import pytest

from factory.audit_corpus import CASES
from factory.audit_report import EXIT_INCOMPLETE, EXIT_REGRESSION, exit_code, load_records, render, wilson

BAD = next(c for c in CASES if c.label == "fail")
GOOD = next(c for c in CASES if c.label == "pass")


def rec(case, run, outcome, findings=(), cost=0.03, assumed=0.0):
    return {"case": case.id, "run": run, "label": case.label, "category": case.category, "outcome": outcome,
            "findings": list(findings), "cost_usd": cost, "assumed_usd": assumed}


def cover(cases, verdicts_by_case, runs=3):
    """Records for every case, defaulting to the right verdict, with overrides {case_id: [outcomes]}."""
    records = []
    for case in cases:
        right = case.label
        for run in range(1, runs + 1):
            outcomes = verdicts_by_case.get(case.id)
            records.append(rec(case, run, outcomes[run - 1] if outcomes else right))
    return records


def test_wilson_matches_known_values_and_is_wide_for_no_data():
    low, high = wilson(0, 13)

    assert low == 0 and high == pytest.approx(0.228, abs=0.002)
    assert wilson(0, 0) == (0.0, 1.0)


def test_a_clean_complete_run_exits_zero():
    assert exit_code(cover(CASES, {}), runs=3, complete=True) == 0


def test_a_bad_case_passed_in_any_single_run_is_a_regression():
    records = cover(CASES, {BAD.id: ["fail", "pass", "fail"]})

    assert exit_code(records, runs=3, complete=True) == EXIT_REGRESSION == 1


def test_a_good_case_failed_by_a_majority_is_a_regression_but_one_failed_run_is_not():
    assert exit_code(cover(CASES, {GOOD.id: ["fail", "fail", "pass"]}), runs=3, complete=True) == 1
    assert exit_code(cover(CASES, {GOOD.id: ["fail", "pass", "pass"]}), runs=3, complete=True) == 0


def test_a_case_with_fewer_than_two_valid_runs_makes_a_three_run_eval_incomplete():
    records = cover(CASES, {GOOD.id: ["pass", "infra:timeout", "infra:timeout"]})

    assert exit_code(records, runs=3, complete=True) == EXIT_INCOMPLETE == 3


def test_an_explicit_single_run_needs_only_one_valid_run_per_case():
    assert exit_code(cover(CASES[:1], {}, runs=1), runs=1, complete=True) == 0


def test_a_run_that_stopped_early_is_incomplete_but_a_regression_still_outranks_it():
    assert exit_code(cover(CASES, {}), runs=3, complete=False) == 3
    assert exit_code(cover(CASES, {BAD.id: ["pass", "fail", "fail"]}), runs=3, complete=False) == 1


def test_a_case_with_no_results_at_all_is_incomplete():
    assert exit_code(cover(CASES[1:], {}), runs=3, complete=True, cases=CASES) == 3


def test_the_report_has_a_table_intervals_infra_counts_and_cost():
    records = cover(CASES, {GOOD.id: ["pass", "infra:timeout", "pass"]})
    records[0]["assumed_usd"] = 0.6

    text = "\n".join(render(records, CASES))

    assert f"{GOOD.id}" in text and "pass ? pass" in text
    assert "false-pass" in text and "0/13" in text and "95%" in text
    assert "infra:timeout" in text and "cost" in text and "assumed" in text


def test_agent_text_in_the_report_has_control_characters_escaped():
    records = cover(CASES, {})
    records[0]["findings"] = ["\x1b[31mred\x1b[0m and \x07 bell"]

    text = "\n".join(render(records, CASES))

    assert "\x1b" not in text and "\x07" not in text and "red" in text


def test_load_records_skips_a_corrupt_file(tmp_path):
    (tmp_path / "case-01-r1.json").write_text(json.dumps(rec(CASES[0], 1, "pass")))
    (tmp_path / "case-02-r1.json").write_text('{"case": "case-02", "ru')
    (tmp_path / "meta.json").write_text("{}")

    assert [r["case"] for r in load_records(tmp_path)] == ["case-01"]
