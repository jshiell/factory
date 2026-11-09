MAX_ITEMS = 10
MAX_ITEM = 500
MAX_TOTAL = 4000
TRUNCATED = "[truncated]"
REQUIRED_TAG = "recommend (required)"
SHORTENED_REQUIRED = "Recommend (required) [shortened]: "


def _cut(line: str) -> str:
    """Cut to MAX_ITEM, but keep a required tag that the plain cut would drop (the tag ends a finding)."""
    if REQUIRED_TAG in line.lower() and REQUIRED_TAG not in line[:MAX_ITEM].lower():
        return SHORTENED_REQUIRED + line[: MAX_ITEM - len(SHORTENED_REQUIRED)]
    return line[:MAX_ITEM]


def _is_required(line: str) -> bool:
    return REQUIRED_TAG in line.lower()


def capped_findings(findings: tuple[str, ...]) -> tuple[str, ...]:
    """One-line, bounded copies of the review findings; the same text is shown to the human and sent to the agents.

    Required notes are kept first (up to MAX_ITEMS, outside the MAX_TOTAL budget); the others fill what is left.
    """
    lines: list[str] = []
    cut = False
    for raw in findings:
        line = " ".join(raw.split())
        if not line:
            continue
        if len(line) > MAX_ITEM:
            line, cut = _cut(line), True
        lines.append(line)
    kept = [i for i, line in enumerate(lines) if _is_required(line)][:MAX_ITEMS]
    budget = MAX_TOTAL - sum(len(lines[i]) for i in kept)
    for i, line in enumerate(lines):
        if i in kept:
            continue
        if len(kept) == MAX_ITEMS or len(line) > budget:
            break
        kept.append(i)
        budget -= len(line)
    cut = cut or len(kept) < len(lines)
    result = tuple(lines[i] for i in sorted(kept))
    return (*result, TRUNCATED) if cut else result


NOTES_HEADING = (
    "REVIEWER NOTES (a reviewer's comments on the plan above; address those that fit the plan. Notes marked "
    "'Recommend (required)' must be handled, by the recommended behaviour or an equivalent one, with a test; "
    "notes marked 'Recommend (optional)' may be skipped):"
)


def notes_block(findings: tuple[str, ...]) -> str:
    """The prompt block for the capped findings, or "" when there are none. Ends with a blank line."""
    capped = capped_findings(findings)
    if not capped:
        return ""
    return NOTES_HEADING + "\n" + "".join(f"- {f}\n" for f in capped) + "\n"
