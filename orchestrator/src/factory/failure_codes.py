from dataclasses import dataclass

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
