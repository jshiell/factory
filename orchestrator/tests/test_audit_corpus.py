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
