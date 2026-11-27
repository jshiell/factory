import os
from collections.abc import Iterable

from factory.process import STRIPPED_ENV

PART_LIMIT = 2000
TOTAL_LIMIT = 6000
CUT_MARKER = "(earlier output cut)\n"
FURTHER_CUT_MARKER = "\n(further evidence cut)"
REDACTED = "[redacted]"


def bounded_evidence(parts: Iterable[tuple[str, str]]) -> str:
    """Labelled blocks of evidence text, each cut to its last `PART_LIMIT` characters and the whole to `TOTAL_LIMIT`.

    The values of the secret environment variables are redacted first, so one cannot survive across a cut."""
    secrets = _secret_values()
    joined = "\n\n".join(f"{label}:\n{_tail(_redacted(text, secrets))}" for label, text in parts)
    if len(joined) <= TOTAL_LIMIT:
        return joined
    return joined[:TOTAL_LIMIT] + FURTHER_CUT_MARKER


def _secret_values() -> tuple[str, ...]:
    return tuple(value for name in STRIPPED_ENV if (value := os.environ.get(name)))


def _redacted(text: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        text = text.replace(secret, REDACTED)
    return text


def _tail(text: str) -> str:
    if len(text) <= PART_LIMIT:
        return text
    return CUT_MARKER + text[-PART_LIMIT:]
