from dataclasses import dataclass

PROMPT_TOO_LARGE = "prompt_too_large"
RUNNER_ERROR = "runner_error"
TIMEOUT = "timeout"
BUDGET_EXHAUSTED = "budget_exhausted"
ERROR = "error"
UNPARSEABLE = "unparseable"
NOT_DESCENDANT = "not_descendant"
NO_COMMITS = "no_commits"
OUTSIDE_PROJECT = "outside_project"
NO_CHANGES = "no_changes"
TESTS_MODIFIED = "tests_modified"
PROTECTED_FILE_CHANGED = "protected_file_changed"
SANDBOX_UNAVAILABLE = "sandbox_unavailable"
TESTS_TIMEOUT = "tests_timeout"
TESTS_FAILED = "tests_failed"
WORKTREE_MISMATCH = "worktree_mismatch"
WORKTREE_NOT_CLEAN = "worktree_not_clean"
NESTED_REPO = "nested_repo"
GATE_MODIFIED_TRACKED_FILES = "gate_modified_tracked_files"
FIX_MOVED = "fix_moved"
FIX_NO_CHANGES = "fix_no_changes"
FIX_INTERRUPTED = "fix_interrupted"
PLAN_MODIFIED_WORKTREE = "plan_modified_worktree"
PLAN_MALFORMED = "plan_malformed"
QUESTIONS_TOO_LARGE = "questions_too_large"
PLAN_TOO_LARGE = "plan_too_large"
PLAN_CHANGED = "plan_changed"
REVIEW_MODIFIED_WORKTREE = "review_modified_worktree"
PLAN_REJECTED = "plan_rejected"
REVIEW_MALFORMED = "review_malformed"
DIFF_TOO_LARGE = "diff_too_large"
AUDIT_MODIFIED_WORKTREE = "audit_modified_worktree"
AUDIT_REJECTED = "audit_rejected"
AUDIT_MALFORMED = "audit_malformed"
PLAN_REJECTED_BY_HUMAN = "plan_rejected_by_human"
ABANDONED = "abandoned"
INTERRUPTED = "interrupted"

FIX_PREFIX = "fix_"
FIX_SENTENCE_PREFIX = "While fixing the failing tests: "


@dataclass(frozen=True)
class Explanation:
    sentence: str
    next_step: str


_CATALOGUE: dict[str, dict[str, Explanation]] = {
    "failure_reason": {
        "tests_failed": Explanation(
            "The tests failed after the agent's change (and any fix rounds).",
            "Read the test output; retry or fix by hand.",
        ),
        "timeout": Explanation("The agent ran out of time.", "Retry the task."),
    },
    "plan_error": {
        "timeout": Explanation(
            "The agent ran out of time.", "Advance again; the task kept its state."
        ),
    },
}


def explain(field: str, code: str) -> Explanation | None:
    found = _CATALOGUE.get(field, {}).get(code)
    if found is None and field == "failure_reason" and code.startswith(FIX_PREFIX):
        inner = explain(field, code.removeprefix(FIX_PREFIX))
        if inner is not None:
            return Explanation(FIX_SENTENCE_PREFIX + inner.sentence, inner.next_step)
    return found
