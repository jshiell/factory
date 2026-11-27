from collections.abc import Iterable

PART_LIMIT = 2000
CUT_MARKER = "(earlier output cut)\n"


def bounded_evidence(parts: Iterable[tuple[str, str]]) -> str:
    """Labelled blocks of evidence text, each cut to its last `PART_LIMIT` characters."""
    return "\n\n".join(f"{label}:\n{_tail(text)}" for label, text in parts)


def _tail(text: str) -> str:
    if len(text) <= PART_LIMIT:
        return text
    return CUT_MARKER + text[-PART_LIMIT:]
