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


def _leaks(case) -> list[str]:
    """Anything in a worktree that tells the auditor the label: the case id or the expected-reason text.
    Categories are not checked: words like "parametrize" are ordinary code."""
    return [path for path, content in case.files.items() if any(s in path or s in content for s in (case.id, case.expected))]


def test_the_leak_check_catches_a_planted_label_hint():
    from dataclasses import replace
    planted = replace(CASES[0], files={**CASES[0].files, "notes.txt": f"this is {CASES[0].expected}"})

    assert _leaks(planted) == ["notes.txt"]


def test_no_case_writes_its_id_or_expected_reason_into_a_worktree_file():
    assert {case.id: _leaks(case) for case in CASES if _leaks(case)} == {}


def test_the_base_project_is_inlined_with_calc_its_test_and_a_pyproject():
    from factory.audit_corpus import BASE_FILES

    assert set(BASE_FILES) == {"pyproject.toml", "src/calc/__init__.py", "tests/test_calc.py", ".gitignore"}
    assert "def add" in BASE_FILES["src/calc/__init__.py"] and "[project]" in BASE_FILES["pyproject.toml"]


def test_case_and_corpus_hashes_are_stable_and_follow_the_content():
    from dataclasses import replace
    from factory.audit_corpus import case_hash, corpus_hash

    first = CASES[0]
    edited = replace(first, files={**first.files, "tests/test_subtract.py": "changed"})
    relabelled = replace(first, label="fail")

    assert case_hash(first) == case_hash(first) and corpus_hash(CASES) == corpus_hash(CASES)
    assert case_hash(first) != case_hash(edited) and case_hash(first) != case_hash(relabelled)
    assert case_hash(CASES[0]) != case_hash(CASES[1])
    assert corpus_hash(CASES) != corpus_hash([edited, *CASES[1:]])
