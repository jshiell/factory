import pytest

from factory import failure_codes
from factory.failure_codes import explain

FAILURE = ("failure_reason",)
STAGE = ("plan_error", "review_error", "audit_error")
EVERYWHERE = FAILURE + STAGE
FIELDS_BY_CONSTANT = {
    "PROMPT_TOO_LARGE": ("failure_reason", "audit_error"),
    "RUNNER_ERROR": EVERYWHERE,
    "TIMEOUT": EVERYWHERE,
    "BUDGET_EXHAUSTED": EVERYWHERE,
    "ERROR": EVERYWHERE,
    "UNPARSEABLE": EVERYWHERE,
    "NOT_DESCENDANT": FAILURE,
    "NO_COMMITS": FAILURE,
    "OUTSIDE_PROJECT": FAILURE,
    "NO_CHANGES": FAILURE,
    "TESTS_MODIFIED": FAILURE,
    "PROTECTED_FILE_CHANGED": FAILURE,
    "SANDBOX_UNAVAILABLE": FAILURE,
    "TESTS_TIMEOUT": FAILURE,
    "TESTS_FAILED": FAILURE,
    "WORKTREE_MISMATCH": FAILURE,
    "WORKTREE_NOT_CLEAN": FAILURE,
    "NESTED_REPO": FAILURE,
    "GATE_MODIFIED_TRACKED_FILES": FAILURE,
    "FIX_MOVED": FAILURE,
    "FIX_NO_CHANGES": FAILURE,
    "FIX_INTERRUPTED": FAILURE,
    "PLAN_MODIFIED_WORKTREE": FAILURE,
    "PLAN_MALFORMED": ("plan_error",),
    "QUESTIONS_TOO_LARGE": ("plan_error",),
    "PLAN_TOO_LARGE": ("plan_error",),
    "PLAN_CHANGED": FAILURE,
    "REVIEW_MODIFIED_WORKTREE": FAILURE,
    "PLAN_REJECTED": FAILURE,
    "REVIEW_MALFORMED": ("review_error",),
    "DIFF_TOO_LARGE": FAILURE,
    "AUDIT_MODIFIED_WORKTREE": FAILURE,
    "AUDIT_REJECTED": FAILURE,
    "AUDIT_MALFORMED": ("audit_error",),
    "PLAN_REJECTED_BY_HUMAN": FAILURE,
    "ABANDONED": FAILURE,
    "INTERRUPTED": FAILURE,
}
NOT_CODES = {"FIX_PREFIX", "FIX_SENTENCE_PREFIX"}


def test_every_code_constant_declares_the_fields_it_is_stored_in():
    constants = {name for name in vars(failure_codes) if name.isupper() and not name.startswith("_") and name not in NOT_CODES}

    assert constants == set(FIELDS_BY_CONSTANT)


@pytest.mark.parametrize(
    ("constant", "field"),
    [(constant, field) for constant, fields in FIELDS_BY_CONSTANT.items() for field in fields],
)
def test_every_code_is_explained_in_each_field_it_is_stored_in(constant, field):
    explanation = explain(field, getattr(failure_codes, constant))

    assert explanation is not None
    assert explanation.sentence
