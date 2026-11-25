from dataclasses import dataclass


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
    },
}


def explain(field: str, code: str) -> Explanation | None:
    return _CATALOGUE.get(field, {}).get(code)
