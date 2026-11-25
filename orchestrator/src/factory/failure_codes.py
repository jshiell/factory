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

_RETRY = "Retry."
_ADVANCE_AGAIN = "Advance again."
_TASK_KEPT_ITS_STATE = "Advance again; the task kept its state."


@dataclass(frozen=True)
class Explanation:
    sentence: str
    next_step: str = ""

    @property
    def text(self) -> str:
        return f"{self.sentence} {self.next_step}".strip()


_PROMPT_TOO_LARGE = Explanation(
    "The prompt for the agent was too large to send.",
    "Shrink the goal or plan; retrying builds the same prompt.",
)

# Codes stored in failure_reason: the task failed.
_FAILURE_REASON = {
    PROMPT_TOO_LARGE: _PROMPT_TOO_LARGE,
    RUNNER_ERROR: Explanation(
        "The agent could not be run (a launch or sandbox fault, see the server log).",
        "Retry; if it repeats, check the server log.",
    ),
    TIMEOUT: Explanation("The agent ran out of time.", "Retry the task."),
    BUDGET_EXHAUSTED: Explanation(
        "The agent used up its budget before finishing.", "Retry with a smaller goal or a higher budget."
    ),
    ERROR: Explanation("The agent reported an error and stopped.", "See the server log; retry."),
    UNPARSEABLE: Explanation("The agent's output could not be read.", _RETRY),
    NOT_DESCENDANT: Explanation("The agent's branch no longer builds on the task's base.", "Rebase or retry."),
    NO_COMMITS: Explanation("The agent finished without committing anything.", _RETRY),
    OUTSIDE_PROJECT: Explanation("The agent changed files outside the project.", "Retry; check the sandbox."),
    NO_CHANGES: Explanation("The agent's commits change nothing.", _RETRY),
    TESTS_MODIFIED: Explanation("The agent edited existing tests, which is not allowed.", _RETRY),
    PROTECTED_FILE_CHANGED: Explanation("The agent changed a protected file.", _RETRY),
    SANDBOX_UNAVAILABLE: Explanation(
        "The test sandbox could not start, so nothing ran.", "Check the sandbox setting, then retry the gate."
    ),
    TESTS_TIMEOUT: Explanation(
        "The test suite did not finish in time.", "Look for a hanging test; retry."
    ),
    TESTS_FAILED: Explanation(
        "The tests failed after the agent's change (and any fix rounds).",
        "Read the test output; retry or fix by hand.",
    ),
    WORKTREE_MISMATCH: Explanation(
        "The task's worktree is not what the server expects (moved or rewritten).",
        "Recover or abandon the task.",
    ),
    WORKTREE_NOT_CLEAN: Explanation("The worktree has uncommitted changes.", "Clean it, then recover."),
    NESTED_REPO: Explanation("The agent created a nested git repository.", _RETRY),
    GATE_MODIFIED_TRACKED_FILES: Explanation(
        "The test run edited tracked files, so the result cannot be trusted.",
        "Fix the tests so they do not write there.",
    ),
    FIX_MOVED: Explanation("A fix round moved the branch head unexpectedly.", _RETRY),
    FIX_NO_CHANGES: Explanation(
        "A fix round changed nothing, so the same tests would fail again.", "Fix by hand or retry."
    ),
    FIX_INTERRUPTED: Explanation("A fix round was cut short by a restart.", "Recover, then advance."),
    PLAN_MODIFIED_WORKTREE: Explanation("The planning agent edited the worktree, which it must not.", _RETRY),
    PLAN_CHANGED: Explanation("The plan changed after review started.", "Re-plan."),
    REVIEW_MODIFIED_WORKTREE: Explanation("The reviewing agent edited the worktree.", _RETRY),
    PLAN_REJECTED: Explanation(
        "The reviewer rejected the plan; the findings list says why.", "Retry with a revised goal."
    ),
    DIFF_TOO_LARGE: Explanation("The change is too large to audit.", "Split the task."),
    AUDIT_MODIFIED_WORKTREE: Explanation("The auditing agent edited the worktree.", "Retry the audit."),
    AUDIT_REJECTED: Explanation(
        "The auditor rejected the change; the audit findings say why.", "Retry or fix by hand."
    ),
    PLAN_REJECTED_BY_HUMAN: Explanation("You rejected the plan."),
    ABANDONED: Explanation("The task was abandoned."),
    INTERRUPTED: Explanation("The server stopped while the task was running.", "Recover it."),
}

# Infra codes stored as a stage error: the stage failed, the task kept its state.
_STAGE_ERROR_INFRA = {
    RUNNER_ERROR: Explanation(_FAILURE_REASON[RUNNER_ERROR].sentence, "Advance again; if it repeats, check the server log."),
    TIMEOUT: Explanation(_FAILURE_REASON[TIMEOUT].sentence, _TASK_KEPT_ITS_STATE),
    BUDGET_EXHAUSTED: _FAILURE_REASON[BUDGET_EXHAUSTED],  # advancing again cannot fix a budget problem
    ERROR: Explanation(_FAILURE_REASON[ERROR].sentence, "See the server log; advance again."),
    UNPARSEABLE: Explanation(_FAILURE_REASON[UNPARSEABLE].sentence, _TASK_KEPT_ITS_STATE),
}

_CATALOGUE: dict[str, dict[str, Explanation]] = {
    "failure_reason": _FAILURE_REASON,
    "plan_error": {
        **_STAGE_ERROR_INFRA,
        PLAN_MALFORMED: Explanation(
            "The plan came back in a form that could not be read.", "Advance again to re-plan."
        ),
        QUESTIONS_TOO_LARGE: Explanation("The planner asked more questions than allowed.", _ADVANCE_AGAIN),
        PLAN_TOO_LARGE: Explanation(
            "The plan is longer than allowed.", "Advance again, or narrow the goal."
        ),
    },
    "review_error": {
        **_STAGE_ERROR_INFRA,
        REVIEW_MALFORMED: Explanation("The review came back unreadable.", _ADVANCE_AGAIN),
    },
    "audit_error": {
        **_STAGE_ERROR_INFRA,
        PROMPT_TOO_LARGE: _PROMPT_TOO_LARGE,
        AUDIT_MALFORMED: Explanation("The audit came back unreadable.", _ADVANCE_AGAIN),
    },
}


def explain(field: str, code: str) -> Explanation | None:
    found = _CATALOGUE.get(field, {}).get(code)
    if found is None and field == "failure_reason" and code.startswith(FIX_PREFIX):
        inner = explain(field, code.removeprefix(FIX_PREFIX))
        if inner is not None:
            return Explanation(FIX_SENTENCE_PREFIX + inner.sentence, inner.next_step)
    return found
