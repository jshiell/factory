import json
from dataclasses import dataclass
from enum import Enum


class OutcomeKind(Enum):
    SUCCESS = "success"
    SUCCESS_WITH_DENIALS = "success_with_denials"
    BUDGET_EXHAUSTED = "budget_exhausted"
    ERROR = "error"
    UNPARSEABLE = "unparseable"


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
    try:
        json.loads(stdout)
    except json.JSONDecodeError:
        pass
    return Outcome(OutcomeKind.UNPARSEABLE, exit_code)
