from dataclasses import dataclass
from enum import Enum

from factory import failure_codes


class OutcomeKind(Enum):
    SUCCESS = "success"
    SUCCESS_WITH_DENIALS = "success_with_denials"
    BUDGET_EXHAUSTED = failure_codes.BUDGET_EXHAUSTED
    ERROR = failure_codes.ERROR
    UNPARSEABLE = failure_codes.UNPARSEABLE


@dataclass(frozen=True)
class Outcome:
    kind: OutcomeKind
    exit_code: int
    cost_usd: float = 0.0
    subtype: str | None = None
    errors: tuple[str, ...] = ()
    denials: tuple[dict, ...] = ()
    structured_output: dict | None = None
    result: str | None = None


def classify(stdout: str, exit_code: int) -> Outcome:
    from factory.harness import ClaudeHarness  # harness.py imports this module

    return ClaudeHarness().parse(stdout, exit_code)
