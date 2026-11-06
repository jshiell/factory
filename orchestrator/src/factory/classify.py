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


def _parse_result(stdout: str) -> dict | None:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("type") != "result":
        return None
    return payload


def classify(stdout: str, exit_code: int) -> Outcome:
    payload = _parse_result(stdout)
    if payload is None:
        return Outcome(OutcomeKind.UNPARSEABLE, exit_code)
    if payload.get("subtype") == "error_max_budget_usd":
        kind = OutcomeKind.BUDGET_EXHAUSTED
    elif payload.get("is_error"):
        kind = OutcomeKind.ERROR
    elif payload.get("permission_denials"):
        kind = OutcomeKind.SUCCESS_WITH_DENIALS
    else:
        kind = OutcomeKind.SUCCESS
    return Outcome(
        kind,
        exit_code,
        cost_usd=payload.get("total_cost_usd", 0.0),
        subtype=payload.get("subtype"),
        errors=tuple(payload.get("errors", ())),
        denials=tuple(payload.get("permission_denials", ())),
        structured_output=payload.get("structured_output"),
        result=payload.get("result"),
    )
