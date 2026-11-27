from collections.abc import Iterable

PART_LIMIT = 2000
TOTAL_LIMIT = 6000
CUT_MARKER = "(earlier output cut)\n"
FURTHER_CUT_MARKER = "\n(further evidence cut)"


def bounded_evidence(parts: Iterable[tuple[str, str]]) -> str:
    """Labelled blocks of evidence text, each cut to its last `PART_LIMIT` characters and the whole to `TOTAL_LIMIT`."""
    joined = "\n\n".join(f"{label}:\n{_tail(text)}" for label, text in parts)
    if len(joined) <= TOTAL_LIMIT:
        return joined
    return joined[:TOTAL_LIMIT] + FURTHER_CUT_MARKER


def _tail(text: str) -> str:
    if len(text) <= PART_LIMIT:
        return text
    return CUT_MARKER + text[-PART_LIMIT:]
