import ast

from factory.audit_corpus import CASES, PROJECT_SUBDIR


def test_there_are_twenty_one_cases_with_unique_ids_and_known_labels():
    assert len(CASES) == 21 and len({case.id for case in CASES}) == 21
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


def test_the_base_pyproject_can_run_the_tests_it_ships_with():
    import tomllib
    from factory.audit_corpus import BASE_FILES

    project = tomllib.loads(BASE_FILES["pyproject.toml"])

    assert project["build-system"]["build-backend"] == "uv_build"
    assert any(dep.startswith("pytest") for dep in project["dependency-groups"]["dev"])


# --- reviewer notes on a case (plan 24 C6) --------------------------------------
from dataclasses import replace

from factory.audit_corpus import case_hash, corpus_hash


def test_guard_the_first_twenty_cases_keep_their_corpus_hash():
    # Characterisation (starts green): adding the notes field must not move the plan-22/23 hashes.
    assert corpus_hash(CASES[:20]) == "2449f1ff319eeef3"


def test_a_case_has_no_reviewer_notes_by_default():
    assert all(case.review_findings == () for case in CASES[:20])


def test_reviewer_notes_are_part_of_what_the_case_hash_identifies():
    case = CASES[0]

    assert case_hash(replace(case, review_findings=("a note",))) != case_hash(case)
    assert case_hash(replace(case, review_findings=("a note",))) != case_hash(replace(case, review_findings=("other",)))


def test_the_ignored_note_case_has_a_note_the_plan_lacks_and_a_change_that_ignores_it():
    case = next(c for c in CASES if c.id == "case-21")

    assert case.label == "fail" and case.review_findings
    assert all("bytes" in note or "bytearray" in note for note in case.review_findings)
    assert "bytes" not in case.plan
    assert not any("bytes" in content for content in case.files.values())
    assert "def mean" in case.files["src/calc/__init__.py"] and "tests/test_mean.py" in case.files


def test_the_ignored_note_case_follows_its_plan_except_for_the_note():
    # The plan must name the files the change actually touches, or the auditor fails it for plan deviations
    # instead of the ignored note (a name collision once gave it another case's stats.py plan).
    case = next(c for c in CASES if c.id == "case-21")

    assert "target/src/calc/__init__.py" in case.plan and "target/tests/test_mean.py" in case.plan
    assert "stats" not in case.plan and "stats" not in case.goal
