import ast

from factory.audit_corpus import CASES, PROJECT_SUBDIR


def test_there_are_twenty_cases_with_unique_ids_and_known_labels():
    assert len(CASES) == 20 and len({case.id for case in CASES}) == 20
    assert {case.label for case in CASES} == {"pass", "fail"}
    assert sum(case.label == "pass" for case in CASES) == 7


def test_every_case_has_a_goal_a_plan_that_names_the_project_and_some_files():
    for case in CASES:
        assert case.goal and case.plan and case.files, case.id
        assert f"{PROJECT_SUBDIR}/" in case.plan, case.id


def test_every_python_file_in_every_case_parses():
    for case in CASES:
        for path, content in case.files.items():
            if path.endswith(".py"):
                ast.parse(content, filename=f"{case.id}/{path}")


def _test_body(case_id: str, path: str) -> str:
    return next(case for case in CASES if case.id == case_id).files[path]


def test_the_honest_bulk_case_has_a_zero_or_negative_row_for_maximum():
    body = _test_body("case-07", "tests/test_ops.py")
    maximum_rows = body.split("def test_maximum")[0].rsplit("@pytest.mark.parametrize", 1)[1]

    assert "-" in maximum_rows or "(0," in maximum_rows.replace(" ", "")


def test_the_tautology_case_keeps_its_tautology_and_the_same_rows():
    honest, bad = _test_body("case-07", "tests/test_ops.py"), _test_body("case-11", "tests/test_ops.py")

    assert "assert maximum(a, b) == maximum(a, b)" in bad
    assert bad.replace("maximum(a, b) == maximum(a, b)", "maximum(a, b) == want") == honest
